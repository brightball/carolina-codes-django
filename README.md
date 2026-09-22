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
