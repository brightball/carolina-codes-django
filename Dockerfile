FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "django>=5.2" "gunicorn>=23.0" "psycopg[binary]>=3.2"
COPY . .
ENV PORT=8080
EXPOSE 8080
CMD ["gunicorn", "--bind", "[::]:8080", "config.wsgi:application"]
