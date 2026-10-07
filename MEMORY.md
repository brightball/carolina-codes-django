# Memory

Operational facts for this Django API. Edit this file in place when a command, version, port, or gotcha changes. Durable choices and rejected alternatives belong in `DECISIONS.md`. Git history is the changelog.

Do not store secrets, production DSNs, tokens, or private hostnames here. The local examples below are the public dev values.

## How to run

```bash
uv sync
DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/carolina_dev \
CAROLINA_URL=http://127.0.0.1:4000 \
POLYGLOT_REGISTER_TOKEN=dev \
PUBLIC_BASE_URL=http://127.0.0.1:4019 \
PORT=4019 \
uv run gunicorn --bind "[::]:4019" config.wsgi:application
```

`GET /health` returns `{"status":"ok"}` and does not open Postgres. `GET /` reports Python and Django. Handler tests use a fake catalog and do not need Postgres.

`manage.py migrate` and `makemigrations` exit 2. Do not point Django `DATABASES` at the catalog.

## Versions

`requires-python` is `>=3.11`. The container image is `python:3.12-slim`. Gitea CI uses `python:3.12-bookworm`.

Declared dependencies are `django>=5.2`, `gunicorn>=23.0`, and `psycopg[binary]>=3.2`. uv.lock selects Django 5.2.17 when Python is below 3.12 and Django 6.1.1 when Python is 3.12 or newer. The image and CI run Python 3.12, so they install Django 6.1.1.

The lockfile pins gunicorn 26.2.0 and psycopg 3.3.5 with the binary extra. Tools: uv 0.11.21, gitleaks 8.30.1, plus ruff, bandit, pip-audit, and pre-commit.

## Ports, pool, and the 256mb VM

Local listen port is 4019. The container and Fly listen port is 8080.

`DJANGO_DB_POOL` defaults to 2. The image runs gunicorn with one worker and two threads. The Fly VM is 256mb. A larger pool on that VM exhausts memory. See `DECISIONS.md`.

## Environment

| Variable | Role |
|---|---|
| `DATABASE_URL` | psycopg DSN for the `v1_*` views |
| `CAROLINA_URL` | CMS base URL. Unset skips registration |
| `POLYGLOT_REGISTER_TOKEN` | Register token. Local example: `dev` |
| `PUBLIC_BASE_URL` | URL the CMS will call |
| `PORT` | Listen port. Local default 4019, container 8080 |
| `DJANGO_DB_POOL` | psycopg pool size. Default 2 |
| `DJANGO_SKIP_REGISTER` | Set to `1` to skip the background register |
| `DJANGO_DEBUG` | Set to `1` to enable Django debug |

Registration accepts only `http` and `https`. A slow CMS must not delay `GET /health`.

## Quality commands

```bash
uv run python manage.py test
uv run bandit -r catalog config manage.py -x catalog/tests.py
uv run pip-audit
gitleaks detect --source . --verbose --no-banner
uv run ruff check .
```

Install the hooks with `uv sync` and `uv run pre-commit install`. Emergency skip: `SKIP=tests,sast,audit,gitleaks,style git commit`.

## Responses

Catalog list bodies are `{ "data": [ ... ] }`. An unknown slug is HTTP 404 with `{"error":"not_found"}`. Every response sets `X-Polyglot-Language` and `X-Polyglot-Framework`.
