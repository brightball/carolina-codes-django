# carolina-codes-django

Read-only v1 polyglot API for the Carolina Code Conference. Django serves ordinary JSON. The Phoenix site (`Carolina.Polyglot`) keeps at most one language API warm and reads speakers and sponsors from it.

This file is the standing contract. Operational facts live in `MEMORY.md`. Durable choices live in `DECISIONS.md`.

## Agent memory

Read `MEMORY.md` and `DECISIONS.md` before changing this API.

- Update `DECISIONS.md` when a durable choice changes. Append an entry with status, date, context, choice, and rejected alternatives. Mark the old entry `superseded`. Do not delete history.
- Update `MEMORY.md` when a fact or command changes. Edit the current fact in place.
- Do not store secrets in either file. Git history is the changelog.

## Workspace

This repository is one sibling git remote in the carolina.codes polyglot fleet. Treat this repo as the workspace root. The Phoenix CMS is a different remote (`github.com/brightball/carolina-codes`). Do not assume `../elixir` or other sibling directories exist. Do not fold this tree into the CMS git remote.

The HTTP contract is the CMS file `priv/api/openapi.yaml` (not in this repo). Do not edit starter-only paths that are not in this repo (`openapi.yaml`, `db/*.sql`, `docker-compose.yml`, `src/`).

## Purpose

1. Query PostgreSQL `v1_*` views with psycopg. Never query Ash resource tables.
2. Expose the routes below. Do not implement Ash JSON:API (`application/vnd.api+json`).
3. Register once on boot, in the background, with no heartbeat. If `CAROLINA_URL` is unset or the POST fails, log and keep serving.

## Environment

| Variable | Example | Role |
|---|---|---|
| `DATABASE_URL` | `postgres://postgres:postgres@127.0.0.1:5432/carolina_dev` | SQL views |
| `CAROLINA_URL` | `http://127.0.0.1:4000` | CMS (optional; register no-ops if unset or down) |
| `POLYGLOT_REGISTER_TOKEN` | `dev` | Register token |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:4019` | URL the CMS will call |
| `PORT` | `4019` | Local listen port. The container and Fly use `8080` |

Further knobs (`DJANGO_DB_POOL`, `DJANGO_SKIP_REGISTER`, `DJANGO_DEBUG`) are in `MEMORY.md`. Install, test, and quality commands use `uv`.

## SQL views (query these only)

`v1_speakers`, `v1_sponsors`, `v1_years`, `v1_talks`, `v1_sponsorships`, `v1_year_speakers`, `v1_year_sponsors`.

Catalog SQL is psycopg against those views (`catalog/db.py`). Django `DATABASES` is in-memory sqlite (`:memory:`). It is not the catalog. `manage.py` refuses `migrate` and `makemigrations`.

Do not `SELECT` from `speakers`, `organizations`, `talks`, or other base tables. The views are the API. Year-scoped speaker rows include `languages` and `topics`. Year-scoped sponsor rows include `tier`.

`photo_path` and `logo_path` are web paths. Return the path. Serving the bytes is optional.

## Required HTTP routes

List bodies are `{ "data": [ ... ] }`. Detail bodies are `{ "data": { ... } }`. Unknown slugs return 404 and `{"error":"not_found"}`.

- `GET /health` — liveness (`{ "status": "ok" }`). Does not touch Postgres or wait on the CMS.
- `GET /` — identity (`language`, `framework`, `api_version`, `endpoints`)
- `GET /v1/years`
- `GET /v1/speakers` and `GET /v1/speakers?year=`
- `GET /v1/speakers/{slug}` and `GET /v1/speakers/{year}/{slug}`
- `GET /v1/sponsors` and `GET /v1/sponsors?year=`
- `GET /v1/sponsors/{slug}` and `GET /v1/sponsors/{year}/{slug}`

## Register on boot (once)

`POST {CAROLINA_URL}/internal/api-endpoints/register`

The JSON body is `catalog.db.identity()` plus `base_url` (`PUBLIC_BASE_URL`). The token is `POLYGLOT_REGISTER_TOKEN`. Local example: `dev`.

`config/wsgi.py` calls `schedule_registration` after the WSGI application exists. That starts one daemon thread. Do not heartbeat. The CMS keep-alives the warm API.

If `CAROLINA_URL` or the token is empty, or the POST fails, log and keep serving. `GET /health` does not wait on that call. Refuse a `CAROLINA_URL` whose scheme is not `http` or `https`.

## Layout

| Path | Role |
|---|---|
| `AGENTS.md` | Standing contract (this file) |
| `MEMORY.md` | Commands, versions, ports, pool |
| `DECISIONS.md` | Accepted choices and rejected alternatives |
| `README.md` | Quickstart and version pins |
| `catalog/db.py` | psycopg against the `v1_*` views, pool of 2 |
| `catalog/views.py` | HTTP handlers |
| `catalog/register.py` | One background register attempt |
| `catalog/tests.py` | API tests and these doc contracts |
| `config/settings.py` | In-memory sqlite `DATABASES` |
| `config/wsgi.py` | Application, then background register |
| `manage.py` | Refuses `migrate` and `makemigrations` |
| `pyproject.toml`, `uv.lock` | Dependencies and the locked Django split |
| `Dockerfile` | Python 3.12 image, listen port 8080 |
| `mise.toml` | uv and gitleaks pins |

Quality is five separate checks (tests, bandit, pip-audit, gitleaks, ruff), not one combined gate. Commands are in `MEMORY.md`.

## Checklist

- [ ] Routes return 200 with `{ "data": [ ... ] }` on lists, and 404 on an unknown slug
- [ ] `?year=` speaker rows include `languages` and `topics`; sponsor rows include `tier`
- [ ] Register runs once in the background; `GET /health` does not wait; no heartbeat
- [ ] No writes; no Ash table names; no Ash JSON:API
- [ ] Catalog SQL stays in psycopg; Django `DATABASES` stays in-memory sqlite
- [ ] `migrate` and `makemigrations` stay refused
- [ ] Update `DECISIONS.md` or `MEMORY.md` when a choice or a fact changes
