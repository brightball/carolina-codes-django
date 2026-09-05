import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, Client

from catalog import db

ROOT = Path(__file__).resolve().parent.parent


class PolyglotApiTests(SimpleTestCase):
    def setUp(self):
        db.reset_counts()
        self.client = Client()

    def test_is_django_not_http_server(self):
        settings_src = (ROOT / "config" / "settings.py").read_text()
        wsgi = (ROOT / "config" / "wsgi.py").read_text()
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn("django", pyproject)
        self.assertIn("DJANGO_SETTINGS_MODULE", wsgi)
        self.assertNotIn("http.server", wsgi)
        self.assertNotIn("http.server", settings_src)
        self.assertEqual(db.LANGUAGE, "Python")
        self.assertEqual(db.FRAMEWORK, "Django")

    def test_catalog_schema_is_not_managed_by_django(self):
        engine = settings.DATABASES["default"]["ENGINE"]
        name = str(settings.DATABASES["default"].get("NAME", ""))
        self.assertIn("sqlite3", engine)
        self.assertEqual(name, ":memory:")
        self.assertNotIn("carolina_dev", name)
        self.assertTrue("v1_speakers" in settings.MIGRATION_MODULES)
        self.assertIsNone(settings.MIGRATION_MODULES["catalog"])
        models_py = ROOT / "catalog" / "models.py"
        self.assertFalse(models_py.exists(), "no ORM models file")
        manage = (ROOT / "manage.py").read_text()
        self.assertIn("makemigrations", manage)
        self.assertIn("refusing django", manage)
        readme = (ROOT / "README.md").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("gunicorn", readme)
        self.assertIn("gunicorn", dockerfile)
        self.assertNotIn("migrate", dockerfile)
        start = readme.split("```bash", 1)[1].split("```", 1)[0]
        self.assertIn("gunicorn", start)
        self.assertNotIn("migrate", start)

    def test_manage_py_refuses_migrate(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "manage.py"), "migrate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("must not alter the shared Postgres catalog", proc.stderr)

    def test_health_without_sql_or_postgres(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("status", response.content.decode())
        self.assertIn("ok", response.content.decode())
        self.assertEqual(body["status"], "ok")
        self.assertEqual(db.SQL_COUNT, 0)
        self.assertEqual(db.CONNECT_COUNT, 0)

    def test_identity_is_python_django_without_sql(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["language"], "Python")
        self.assertEqual(body["framework"], "Django")
        self.assertEqual(db.SQL_COUNT, 0)

    def test_unknown_speaker_slug_404(self):
        self._ensure_catalog()
        response = self.client.get("/v1/speakers/no-such-slug")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not_found", response.content.decode())

    def test_year_scoped_speakers_include_languages_topics(self):
        self._ensure_catalog()
        db.reset_counts()
        response = self.client.get("/v1/speakers", {"year": "2026"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("data", payload)
        self.assertIsInstance(payload["data"], list)
        self.assertTrue(payload["data"], "year-scoped speakers returned rows")
        row = payload["data"][0]
        self.assertIn("languages", row)
        self.assertIn("topics", row)
        self.assertIsInstance(row["languages"], list)
        self.assertIsInstance(row["topics"], list)
        self.assertGreater(db.SQL_COUNT, 0)

    def test_year_scoped_sponsors_include_tier(self):
        self._ensure_catalog()
        response = self.client.get("/v1/sponsors", {"year": "2026"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("data", payload)
        self.assertTrue(payload["data"])
        self.assertIn("tier", payload["data"][0])

    def test_parallel_year_lists_use_pool(self):
        self._ensure_catalog()
        if db.QUERY_FN:
            self.skipTest("postgres unavailable")
        errors = []
        speakers = {}
        sponsors = {}

        def load_speakers():
            try:
                speakers["r"] = self.client.get("/v1/speakers", {"year": "2026"})
            except Exception as exc:
                errors.append(exc)

        def load_sponsors():
            try:
                sponsors["r"] = self.client.get("/v1/sponsors", {"year": "2026"})
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=load_speakers)
        t2 = threading.Thread(target=load_sponsors)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors, [])
        self.assertEqual(speakers["r"].status_code, 200)
        self.assertIn("languages", speakers["r"].json()["data"][0])
        self.assertEqual(sponsors["r"].status_code, 200)
        self.assertIn("tier", sponsors["r"].json()["data"][0])

    def _ensure_catalog(self):
        try:
            db.with_cursor(lambda cur: db.db_query(cur, "SELECT 1 AS ok"))
        except Exception:
            def fake(sql, args):
                if "FROM v1_speakers WHERE slug =" in sql:
                    return []
                if "FROM v1_speakers" in sql:
                    return [
                        {
                            "slug": "diana-pham",
                            "first_name": "Diana",
                            "last_name": "Pham",
                            "name": "Diana Pham",
                        }
                    ]
                if "FROM v1_talks" in sql:
                    return [
                        {
                            "slug": "talk",
                            "title": "Talk",
                            "speaker_slug": "diana-pham",
                            "year": 2026,
                            "languages": ["python"],
                            "topics": ["development"],
                        }
                    ]
                if "FROM v1_year_sponsors" in sql:
                    return [
                        {
                            "slug": "flywheel",
                            "name": "Flywheel",
                            "tier": "platinum",
                            "year": 2026,
                        }
                    ]
                if "FROM v1_sponsors WHERE slug" in sql:
                    return []
                return []

            db.QUERY_FN = fake
            db.CONNECT_FN = lambda: (_ for _ in ()).throw(RuntimeError("fake connect"))
