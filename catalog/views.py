from django.http import JsonResponse

from catalog import db


def _json(payload, status=200):
    return JsonResponse(payload, status=status, json_dumps_params={"default": str})


def not_found(request, exception=None):
    return _json({"error": "not_found"}, 404)


def health(request):
    return _json({"status": "ok"})


def identity(request):
    return _json(db.identity())


def years(request):
    def run(cur):
        rows = db.db_query(cur, "SELECT year, slug, name, status FROM v1_years ORDER BY year DESC")
        return [db.clean(r) for r in rows]

    return _json({"data": db.with_cursor(run)})


def speakers(request):
    raw = request.GET.get("year")
    year = int(raw) if raw else None

    def run(cur):
        return db.list_speakers(cur, year)

    return _json({"data": db.with_cursor(run)})


def speaker_detail(request, slug):
    def run(cur):
        speaker = db.load_speaker(cur, slug)
        if not speaker:
            return None
        speaker["talks"] = db.talks_for(cur, slug)
        speaker["years"] = db.talk_years(cur, slug)
        return speaker

    speaker = db.with_cursor(run)
    if not speaker:
        return _json({"error": "not_found"}, 404)
    return _json({"data": speaker})


def speaker_year(request, year, slug):
    def run(cur):
        speaker = db.load_speaker(cur, slug)
        if not speaker:
            return None
        talks = db.talks_for(cur, slug, year)
        if not talks:
            return False
        years = db.talk_years(cur, slug)
        speaker.update(
            {
                "year": year,
                "years": years,
                "other_years": [n for n in years if n != year],
                "talks": talks,
                "languages": db.uniq_tags(talks, "languages"),
                "topics": db.uniq_tags(talks, "topics"),
            }
        )
        return speaker

    speaker = db.with_cursor(run)
    if speaker is None or speaker is False:
        return _json({"error": "not_found"}, 404)
    return _json({"data": speaker})


def sponsors(request):
    raw = request.GET.get("year")

    def run(cur):
        if raw:
            rows = db.db_query(
                cur,
                db.sql_select(
                    db.YEAR_SPONSOR_COLS,
                    "v1_year_sponsors",
                    "WHERE year = %s ORDER BY name",
                ),
                (int(raw),),
            )
        else:
            rows = db.db_query(
                cur,
                db.sql_select(db.SPONSOR_COLS, "v1_sponsors", "ORDER BY name"),
            )
        return [db.clean(r) for r in rows]

    return _json({"data": db.with_cursor(run)})


def sponsor_detail(request, slug):
    def run(cur):
        row = db.clean(
            db.db_query_one(
                cur,
                db.sql_select(db.SPONSOR_COLS, "v1_sponsors", "WHERE slug = %s"),
                (slug,),
            )
        )
        if not row:
            return None
        rows = db.db_query(cur, "SELECT * FROM v1_sponsorships WHERE sponsor_slug = %s", (slug,))
        row["sponsorships"] = [db.clean(r) for r in rows]
        return row

    row = db.with_cursor(run)
    if not row:
        return _json({"error": "not_found"}, 404)
    return _json({"data": row})


def sponsor_year(request, year, slug):
    def run(cur):
        row = db.clean(
            db.db_query_one(
                cur,
                db.sql_select(
                    db.YEAR_SPONSOR_COLS,
                    "v1_year_sponsors",
                    "WHERE year = %s AND slug = %s",
                ),
                (year, slug),
            )
        )
        if not row:
            return None
        years = db.sponsor_years(cur, slug)
        row["years"] = years
        row["other_years"] = [n for n in years if n != year]
        return row

    row = db.with_cursor(run)
    if not row:
        return _json({"error": "not_found"}, 404)
    return _json({"data": row})
