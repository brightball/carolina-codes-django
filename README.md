# carolina-codes-django

Read-only v1 polyglot API for Carolina Code Conference. **Django** + gunicorn. Distinct from `../python` (`http.server` on :4004).

Queries PostgreSQL `v1_*` views via psycopg. Django’s `DATABASES` setting is in-memory sqlite so `migrate` cannot touch the catalog; `manage.py migrate` is refused. The process registers with Elixir once in the background and answers `GET /health` without waiting on that call.

```bash
uv sync
DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/carolina_dev \
CAROLINA_URL=http://127.0.0.1:4000 \
POLYGLOT_REGISTER_TOKEN=dev \
PUBLIC_BASE_URL=http://127.0.0.1:4019 \
PORT=4019 \
uv run gunicorn --bind "[::]:4019" config.wsgi:application
```

`GET /` reports `language: "Python"` and `framework: "Django"`. `GET /health` returns `{"status":"ok"}` without touching Postgres.

## Versions

`requires-python` is `>=3.11`. The container image is `python:3.12-slim`. Gitea CI uses `python:3.12-bookworm`.

Declared dependencies are `django>=5.2`, `gunicorn>=23.0`, and `psycopg[binary]>=3.2`. uv.lock selects Django 5.2.17 when Python is below 3.12 and Django 6.1.1 when Python is 3.12 or newer. The image and CI run Python 3.12, so they install Django 6.1.1.

The lockfile pins gunicorn 26.2.0 and psycopg 3.3.5 with the binary extra. Tools on this repo are uv 0.11.21, ruff, bandit, pip-audit, pre-commit, and gitleaks 8.30.1.

## Quality checks

Install git hooks once (`gitleaks` must be on PATH; `mise.toml` pins it):

```bash
uv sync
uv run pre-commit install
```

`git commit` then runs all five checks. Emergency skip: `SKIP=tests,sast,audit,gitleaks,style git commit`.

The same five checks run as parallel Gitea Actions jobs after a shared prep stage:

```bash
uv run python manage.py test
uv run bandit -r catalog config manage.py -x catalog/tests.py
uv run pip-audit
gitleaks detect --source . --verbose --no-banner
uv run ruff check .
```

Or together: `uv run pre-commit run --all-files`.
