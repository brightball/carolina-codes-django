# Decisions

Durable choices for this Django API. Git history is the changelog.

This is a single decision log, not a `docs/decisions/` tree. Django does not keep one for an application. Each entry is a short architecture decision record: status, date, context, choice, and rejected alternatives.

When a choice changes, append a new entry, set the old status to `superseded`, and name the replacement. Do not delete accepted history. Do not store secrets, production DSNs, or tokens.

## 1. Read-only v1 SQL instead of the Django ORM

- Status: accepted
- Date: 2026-09-05
- Context: The catalog is a shared Postgres database owned by the Phoenix CMS. This process is a read-only API over the `v1_*` views.
- Choice: Catalog reads are psycopg SQL against those views only. Django `DATABASES` is in-memory sqlite. `manage.py` refuses `migrate` and `makemigrations`.
- Rejected: Django ORM models, migrations against the shared Postgres catalog, and queries against Ash tables.

## 2. Registration off the request path

- Status: accepted
- Date: 2026-09-22
- Context: The process must register once with the CMS and then serve traffic. A startup call on the listen path held `GET /health` until the CMS answered. The scheme guard (2026-09-15) still applies.
- Choice: `schedule_registration` starts one background attempt after the WSGI application exists. There is no heartbeat. `GET /health` does not wait on the CMS. If `CAROLINA_URL` is unset or the POST fails, the process logs and keeps serving. Only `http` and `https` URLs are posted.
- Rejected: calling the CMS on the listen path before the worker accepts connections, registering inside a request, a heartbeat loop, and posting to a non-http `CAROLINA_URL`.

## 3. Five quality checks instead of one combined gate

- Status: accepted
- Date: 2026-09-15
- Context: Commits and CI need tests, security scanning, dependency audit, secret scanning, and style. One bundled command hid which check failed and blocked parallel CI.
- Choice: tests (`manage.py test`), bandit, pip-audit, gitleaks, and ruff each run as their own pre-commit hook and their own parallel CI job.
- Rejected: a single `pre-commit run --all-files` gate, and folding security into ruff.

## 4. Two-connection pool on a 256mb machine

- Status: accepted
- Date: 2026-09-05
- Context: The Fly VM is 256mb with one shared CPU. Extra workers or a large Postgres pool use that memory on first checkout.
- Choice: one gunicorn worker, two threads, and a psycopg pool of 2 (`DJANGO_DB_POOL`).
- Rejected: several gunicorn workers, and an unbounded connection pool, on the 256mb VM.
