FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY catalog ./catalog
COPY config ./config
RUN /app/.venv/bin/python -m compileall -q catalog config

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080 \
    DJANGO_DB_POOL=2 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["gunicorn", "--bind", "[::]:8080", "--workers", "1", "--threads", "2", "--timeout", "30", "--graceful-timeout", "20", "config.wsgi:application"]
