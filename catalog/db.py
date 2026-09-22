"""Read-only SQL against PostgreSQL v1_* views. No Django ORM / migrations."""

from __future__ import annotations

import os
import sys
import threading

LANGUAGE = "Python"
FRAMEWORK = "Django"
API_VERSION = "0.2.0"
CREATED_YEAR = 2026
SCHEMA_VERSION = 1
LANGUAGE_VERSION = sys.version.split()[0]

ENDPOINTS = [
    {"method": "GET", "path": "/", "query": []},
    {"method": "GET", "path": "/health", "query": []},
    {"method": "GET", "path": "/v1/years", "query": []},
    {"method": "GET", "path": "/v1/speakers", "query": ["year"]},
    {"method": "GET", "path": "/v1/speakers/:slug", "query": []},
    {"method": "GET", "path": "/v1/speakers/:year/:slug", "query": []},
    {"method": "GET", "path": "/v1/sponsors", "query": ["year"]},
    {"method": "GET", "path": "/v1/sponsors/:slug", "query": []},
    {"method": "GET", "path": "/v1/sponsors/:year/:slug", "query": []},
]

SPEAKER_COLS = (
    "slug, first_name, last_name, name, tagline, bio, company, location, "
    "photo_path, twitter_url, linkedin_url, website_url, github_url, featured"
)
YEAR_SPONSOR_COLS = (
    "slug, name, website, logo_path, description, blurb, tier, featured, year, "
    "twitter_url, linkedin_url, youtube_url, instagram_url, facebook_url"
)
SPONSOR_COLS = (
    "slug, name, website, logo_path, description, twitter_url, linkedin_url, youtube_url, instagram_url, facebook_url"
)
TALK_COLS = "slug, title, description, format, youtube_id, year, speaker_slug, languages, topics"

# One gunicorn process on a 256mb machine. Two connections cover the two
# request threads without opening a pile of Postgres sessions at first use.
POOL_SIZE = int(os.environ.get("DJANGO_DB_POOL", "2"))
SQL_COUNT = 0
CONNECT_COUNT = 0
CONNECT_FN = None
QUERY_FN = None

_pool_lock = threading.Condition()
_idle: list = []
_opened = 0
_count_lock = threading.Lock()


def reset_counts() -> None:
    global SQL_COUNT, CONNECT_COUNT
    with _count_lock:
        SQL_COUNT = 0
        CONNECT_COUNT = 0


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL is required for catalog SQL")
    if "sslmode=" not in raw:
        raw += ("&" if "?" in raw else "?") + "sslmode=disable"
    return raw


def open_connection():
    global CONNECT_COUNT
    with _count_lock:
        CONNECT_COUNT += 1
    if CONNECT_FN:
        return CONNECT_FN()
    target = dsn()
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(target, row_factory=dict_row, autocommit=True)


def acquire():
    global _opened
    with _pool_lock:
        while True:
            if _idle:
                return _idle.pop()
            if _opened < POOL_SIZE:
                _opened += 1
                try:
                    return open_connection()
                except Exception:
                    _opened -= 1
                    raise
            _pool_lock.wait()


def release(conn) -> None:
    if conn is None:
        return
    with _pool_lock:
        _idle.append(conn)
        _pool_lock.notify()


def db_query(cur, sql: str, args=None):
    global SQL_COUNT
    with _count_lock:
        SQL_COUNT += 1
    if QUERY_FN:
        return QUERY_FN(sql, args)
    if args is None:
        cur.execute(sql)
    else:
        cur.execute(sql, args)
    return list(cur.fetchall())


def db_query_one(cur, sql: str, args=None):
    rows = db_query(cur, sql, args)
    return rows[0] if rows else None


def with_cursor(fn):
    if QUERY_FN is not None:
        return fn(None)
    conn = acquire()
    try:
        with conn.cursor() as cur:
            return fn(cur)
    finally:
        release(conn)


def clean(row):
    if row is None:
        return None
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = str(v)
        elif isinstance(v, list):
            out[k] = [str(x) for x in v]
        else:
            out[k] = v
    return out


def uniq_tags(talks, key):
    seen, out = set(), []
    for talk in talks:
        for val in talk.get(key) or []:
            if val and val not in seen:
                seen.add(val)
                out.append(val)
    return out


def sql_select(cols: str, relation: str, suffix: str = "") -> str:
    # cols/relation are module constants; WHERE values bind with %s at execute.
    sql = "SELECT " + cols + " FROM " + relation  # nosec B608
    if suffix:
        sql += " " + suffix
    return sql


def talks_for(cur, slug, year=None):
    if year is None:
        rows = db_query(
            cur,
            sql_select(TALK_COLS, "v1_talks", "WHERE speaker_slug = %s ORDER BY year DESC"),
            (slug,),
        )
    else:
        rows = db_query(
            cur,
            sql_select(
                TALK_COLS,
                "v1_talks",
                "WHERE speaker_slug = %s AND year = %s ORDER BY year DESC",
            ),
            (slug, year),
        )
    return [clean(r) for r in rows]


def talk_years(cur, slug):
    rows = db_query(
        cur,
        "SELECT DISTINCT year FROM v1_talks WHERE speaker_slug = %s ORDER BY year DESC",
        (slug,),
    )
    return [r["year"] for r in rows]


def sponsor_years(cur, slug):
    rows = db_query(
        cur,
        "SELECT DISTINCT year FROM v1_sponsorships WHERE sponsor_slug = %s ORDER BY year DESC",
        (slug,),
    )
    return [r["year"] for r in rows]


def load_speaker(cur, slug):
    return clean(db_query_one(cur, sql_select(SPEAKER_COLS, "v1_speakers", "WHERE slug = %s"), (slug,)))


def load_talks_for_year(cur, year):
    rows = db_query(
        cur,
        sql_select(TALK_COLS, "v1_talks", "WHERE year = %s ORDER BY speaker_slug, year DESC"),
        (year,),
    )
    out = {}
    for row in rows:
        talk = clean(row)
        slug = talk.get("speaker_slug") or ""
        out.setdefault(slug, []).append(talk)
    return out


def load_years_for_slugs(cur, slugs):
    if not slugs:
        return {}
    rows = db_query(
        cur,
        "SELECT DISTINCT speaker_slug, year FROM v1_talks "
        "WHERE speaker_slug = ANY(%s) ORDER BY speaker_slug, year DESC",
        (list(slugs),),
    )
    out = {}
    for row in rows:
        out.setdefault(row["speaker_slug"], []).append(row["year"])
    return out


def attach_year_tags(cur, speakers, year):
    if not speakers:
        return speakers
    slugs = [sp["slug"] for sp in speakers]
    talks_by = load_talks_for_year(cur, year)
    years_by = load_years_for_slugs(cur, slugs)
    for sp in speakers:
        slug = sp["slug"]
        talks = talks_by.get(slug, [])
        years = years_by.get(slug, [])
        sp.update(
            {
                "year": year,
                "talks": talks,
                "languages": uniq_tags(talks, "languages"),
                "topics": uniq_tags(talks, "topics"),
                "years": years,
            }
        )
    return speakers


def list_speakers(cur, year=None):
    if year is None:
        rows = db_query(
            cur,
            sql_select(SPEAKER_COLS, "v1_speakers", "ORDER BY last_name, first_name"),
        )
        return [clean(r) for r in rows]
    rows = db_query(
        cur,
        sql_select(
            SPEAKER_COLS,
            "v1_speakers",
            "WHERE slug IN (SELECT speaker_slug FROM v1_talks WHERE year = %s) ORDER BY last_name, first_name",
        ),
        (year,),
    )
    return attach_year_tags(cur, [clean(r) for r in rows], year)


def identity():
    return {
        "language": LANGUAGE,
        "language_version": LANGUAGE_VERSION,
        "api_version": API_VERSION,
        "framework": FRAMEWORK,
        "created_year": CREATED_YEAR,
        "schema_version": SCHEMA_VERSION,
        "endpoints": ENDPOINTS,
    }
