# carolina-codes-django

Read-only v1 polyglot API for Carolina Code Conference. **Django** + gunicorn. Distinct from `../python` (`http.server` on :4004).

Queries PostgreSQL `v1_*` views via psycopg. Django’s `DATABASES` setting is in-memory sqlite so `migrate` cannot touch the catalog; `manage.py migrate` is refused. Registers with Elixir once on WSGI boot.

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

```bash
uv run python manage.py test
```
