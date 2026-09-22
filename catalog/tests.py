import gzip
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import time
import tomllib
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import Client, SimpleTestCase

from catalog import db
from catalog.register import register_with_elixir

ROOT = Path(__file__).resolve().parent.parent


class PolyglotApiTests(SimpleTestCase):
    def setUp(self):
        db.reset_counts()
        db.QUERY_FN = None
        db.CONNECT_FN = None
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

    def test_register_refuses_non_http_scheme(self):
        env = {"CAROLINA_URL": "file:///etc/passwd", "POLYGLOT_REGISTER_TOKEN": "dev"}
        with patch.dict(os.environ, env), patch("sys.stderr", new=StringIO()) as err:
            register_with_elixir()
        self.assertIn("refused scheme", err.getvalue())

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
        self.assertEqual(db.CONNECT_COUNT, 0)

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

    def test_advertised_routes_return_catalog_json(self):
        self._ensure_catalog()
        db.reset_counts()

        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        payload = root.json()
        self.assertEqual(payload["language"], "Python")
        self.assertEqual(payload["framework"], "Django")
        self.assertEqual(db.SQL_COUNT, 0)
        self.assertEqual(db.CONNECT_COUNT, 0)

        db.reset_counts()
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(db.SQL_COUNT, 0)
        self.assertEqual(db.CONNECT_COUNT, 0)

        speakers = self.client.get("/v1/speakers")
        sponsors = self.client.get("/v1/sponsors")
        years = self.client.get("/v1/years")
        self.assertEqual(speakers.status_code, 200, speakers.content)
        self.assertEqual(sponsors.status_code, 200, sponsors.content)
        self.assertEqual(years.status_code, 200, years.content)
        speaker_rows = speakers.json()["data"]
        sponsor_rows = sponsors.json()["data"]
        year_rows = years.json()["data"]
        self.assertIsInstance(speaker_rows, list)
        self.assertIsInstance(sponsor_rows, list)
        self.assertIsInstance(year_rows, list)
        self.assertTrue(speaker_rows)
        self.assertTrue(sponsor_rows)
        self.assertTrue(year_rows)

        year = int(year_rows[0]["year"])
        year_speakers = self.client.get("/v1/speakers", {"year": str(year)})
        year_sponsors = self.client.get("/v1/sponsors", {"year": str(year)})
        self.assertEqual(year_speakers.status_code, 200, year_speakers.content)
        self.assertEqual(year_sponsors.status_code, 200, year_sponsors.content)
        year_speaker_rows = year_speakers.json()["data"]
        year_sponsor_rows = year_sponsors.json()["data"]
        self.assertIsInstance(year_speaker_rows, list)
        self.assertIsInstance(year_sponsor_rows, list)
        self.assertTrue(year_speaker_rows)
        self.assertTrue(year_sponsor_rows)

        speaker_slug = speaker_rows[0]["slug"]
        sponsor_slug = sponsor_rows[0]["slug"]
        year_speaker_slug = year_speaker_rows[0]["slug"]
        year_sponsor_slug = year_sponsor_rows[0]["slug"]
        speaker_year = int(year_speaker_rows[0].get("year", year))
        sponsor_year = int(year_sponsor_rows[0].get("year", year))
        concrete = {
            "/": "/",
            "/health": "/health",
            "/v1/years": "/v1/years",
            "/v1/speakers": "/v1/speakers",
            "/v1/speakers/:slug": f"/v1/speakers/{speaker_slug}",
            "/v1/speakers/:year/:slug": f"/v1/speakers/{speaker_year}/{year_speaker_slug}",
            "/v1/sponsors": "/v1/sponsors",
            "/v1/sponsors/:slug": f"/v1/sponsors/{sponsor_slug}",
            "/v1/sponsors/:year/:slug": f"/v1/sponsors/{sponsor_year}/{year_sponsor_slug}",
        }

        seen = set()
        for endpoint in payload["endpoints"]:
            self.assertEqual(endpoint["method"], "GET")
            path = endpoint["path"]
            self.assertIn(path, concrete, path)
            self.assertNotIn(path, seen, path)
            seen.add(path)
            url = concrete[path]
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            body = response.json()
            if path == "/health":
                self.assertEqual(body["status"], "ok")
            elif path == "/":
                self.assertEqual(body["language"], "Python")
                self.assertEqual(body["framework"], "Django")
            elif ":slug" in path:
                self.assertIsInstance(body["data"], dict, url)
                self.assertIn("slug", body["data"])
            else:
                self.assertIsInstance(body["data"], list, url)
            query = endpoint.get("query") or []
            if "year" in query:
                listed = self.client.get(path, {"year": str(year)})
                self.assertEqual(listed.status_code, 200, path)
                self.assertIsInstance(listed.json()["data"], list)

        self.assertEqual(seen, set(concrete))

        for url in (
            "/v1/speakers/no-such-speaker-slug",
            "/v1/sponsors/no-such-sponsor-slug",
            f"/v1/speakers/{speaker_year}/no-such-speaker-slug",
            f"/v1/sponsors/{sponsor_year}/no-such-sponsor-slug",
        ):
            missing = self.client.get(url)
            self.assertEqual(missing.status_code, 404, url)
            self.assertEqual(missing.json(), {"error": "not_found"})

        sql_before = db.SQL_COUNT
        connect_before = db.CONNECT_COUNT
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(db.SQL_COUNT, sql_before)
        self.assertEqual(db.CONNECT_COUNT, connect_before)

    def _ensure_catalog(self):
        try:
            db.with_cursor(lambda cur: db.db_query(cur, "SELECT 1 AS ok"))
        except Exception:
            speaker = {
                "slug": "diana-pham",
                "first_name": "Diana",
                "last_name": "Pham",
                "name": "Diana Pham",
            }
            talk = {
                "slug": "talk",
                "title": "Talk",
                "speaker_slug": "diana-pham",
                "year": 2026,
                "languages": ["python"],
                "topics": ["development"],
            }
            year_sponsor = {
                "slug": "flywheel",
                "name": "Flywheel",
                "tier": "platinum",
                "year": 2026,
            }
            sponsor = {
                "slug": "flywheel",
                "name": "Flywheel",
                "website": "https://example.com",
            }

            def fake(sql, args):
                args = args or ()
                if "FROM v1_years" in sql:
                    return [{"year": 2026, "slug": "2026", "name": "2026", "status": "current"}]
                if "FROM v1_speakers WHERE slug =" in sql:
                    if args and args[0] == "diana-pham":
                        return [speaker]
                    return []
                if "FROM v1_speakers" in sql:
                    return [speaker]
                if "FROM v1_talks" in sql:
                    if "speaker_slug = %s AND year = %s" in sql:
                        if len(args) >= 2 and args[0] == "diana-pham" and int(args[1]) == 2026:
                            return [talk]
                        return []
                    if "ANY" in sql:
                        return [{"speaker_slug": "diana-pham", "year": 2026}]
                    if "speaker_slug = %s" in sql:
                        if args and args[0] == "diana-pham":
                            return [talk]
                        return []
                    if "WHERE year = %s" in sql:
                        if args and int(args[0]) == 2026:
                            return [talk]
                        return []
                    return [talk]
                if "FROM v1_year_sponsors" in sql:
                    if "slug = %s" in sql:
                        if len(args) >= 2 and int(args[0]) == 2026 and args[1] == "flywheel":
                            return [year_sponsor]
                        return []
                    if "WHERE year = %s" in sql:
                        if args and int(args[0]) == 2026:
                            return [year_sponsor]
                        return []
                    return [year_sponsor]
                # v1_sponsorships contains the substring v1_sponsors; match it first.
                if "FROM v1_sponsorships" in sql:
                    if not args or args[0] != "flywheel":
                        return []
                    if "DISTINCT year" in sql:
                        return [{"year": 2026}]
                    return [{"sponsor_slug": "flywheel", "year": 2026, "tier": "platinum"}]
                if "FROM v1_sponsors WHERE slug" in sql:
                    if args and args[0] == "flywheel":
                        return [sponsor]
                    return []
                if "FROM v1_sponsors" in sql:
                    return [sponsor]
                return []

            db.QUERY_FN = fake
            db.CONNECT_FN = lambda: (_ for _ in ()).throw(RuntimeError("fake connect"))


class ColdStartConfigTests(SimpleTestCase):
    def test_fly_keeps_one_machine_and_checks_health(self):
        cfg = tomllib.loads((ROOT / "fly.toml").read_text())
        http = cfg["http_service"]
        minimum = http["min_machines_running"]
        self.assertIsInstance(minimum, int)
        self.assertGreaterEqual(minimum, 1)
        self.assertIs(http["auto_start_machines"], True)
        self.assertEqual(http["internal_port"], 8080)
        self.assertEqual(cfg["env"]["PORT"], "8080")
        checks = http["checks"]
        self.assertTrue(any(item.get("method") == "GET" and item.get("path") == "/health" for item in checks))

    def test_image_installs_locked_deps_without_local_venv(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("uv.lock", dockerfile)
        self.assertIn("uv sync --frozen", dockerfile)
        self.assertIn("gunicorn", dockerfile)
        self.assertNotIn("migrate", dockerfile)
        self.assertNotRegex(dockerfile, r"(?i)pip install[^\n]*(django|gunicorn|psycopg)")
        for line in dockerfile.splitlines():
            if not line.strip().startswith("COPY") or "--from=" in line:
                continue
            self.assertNotIn(".venv", line)
            self.assertNotIn(".git", line)
        ignore = (ROOT / ".dockerignore").read_text().splitlines()
        self.assertIn(".venv", ignore)
        self.assertIn(".git", ignore)
        self.assertTrue(any("__pycache__" in line or line == ".ruff_cache" for line in ignore))

    def test_health_is_served_while_registration_hangs(self):
        peer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        peer.bind(("127.0.0.1", 0))
        peer.listen(1)
        peer_port = peer.getsockname()[1]
        accepted = threading.Event()
        stop = threading.Event()
        times = {}

        def stall():
            try:
                conn, _ = peer.accept()
            except OSError:
                return
            times["accept"] = time.perf_counter()
            accepted.set()
            stop.wait(30)
            try:
                conn.close()
            except OSError:
                pass

        stall_thread = threading.Thread(target=stall, daemon=True)
        stall_thread.start()

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        app_port = probe.getsockname()[1]
        probe.close()

        env = os.environ.copy()
        env.pop("DATABASE_URL", None)
        env.pop("DJANGO_SKIP_REGISTER", None)
        env["CAROLINA_URL"] = f"http://127.0.0.1:{peer_port}"
        env["POLYGLOT_REGISTER_TOKEN"] = "dev"
        env["PUBLIC_BASE_URL"] = f"http://127.0.0.1:{app_port}"
        env["PORT"] = str(app_port)
        env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        env["PYTHONUNBUFFERED"] = "1"

        log = tempfile.TemporaryFile(mode="w+")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "--bind",
                f"127.0.0.1:{app_port}",
                "--workers",
                "1",
                "--threads",
                "2",
                "--timeout",
                "30",
                "config.wsgi:application",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def logs():
            log.seek(0)
            return log.read()

        try:
            deadline = time.perf_counter() + 15
            status = None
            raw = b""
            while time.perf_counter() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{app_port}/health", timeout=0.5) as resp:
                        status = resp.status
                        raw = resp.read()
                        times["health"] = time.perf_counter()
                        break
                except (urllib.error.URLError, TimeoutError, OSError):
                    time.sleep(0.05)
            self.assertIsNotNone(times.get("health"), f"gunicorn did not serve /health\n{logs()}")
            self.assertEqual(status, 200, logs())
            self.assertEqual(json.loads(raw.decode())["status"], "ok")
            self.assertTrue(accepted.wait(5), f"registration never connected\n{logs()}")
            self.assertLess(times["health"] - times["accept"], 2.0, logs())

            started = time.perf_counter()
            with urllib.request.urlopen(f"http://127.0.0.1:{app_port}/health", timeout=2) as resp:
                health = json.loads(resp.read().decode())
                self.assertEqual(resp.status, 200)
            self.assertLess(time.perf_counter() - started, 2.0)
            self.assertEqual(health["status"], "ok")

            started = time.perf_counter()
            with urllib.request.urlopen(f"http://127.0.0.1:{app_port}/", timeout=2) as resp:
                identity = json.loads(resp.read().decode())
                self.assertEqual(resp.status, 200)
            self.assertLess(time.perf_counter() - started, 2.0)
            self.assertEqual(identity["language"], "Python")
            self.assertEqual(identity["framework"], "Django")
        finally:
            stop.set()
            try:
                peer.close()
            except OSError:
                pass
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=5)
            stall_thread.join(timeout=2)
            log.close()


CHECK_IDS = ("tests", "sast", "audit", "gitleaks", "style")
CHECK_COMMANDS = {
    "tests": "uv run python manage.py test",
    "sast": "uv run bandit -r catalog config manage.py -x catalog/tests.py",
    "audit": "uv run pip-audit",
    "gitleaks": "gitleaks detect --source . --verbose --no-banner",
    "style": "uv run ruff check .",
}
CLONE_CMD = 'git clone --depth 1 --no-checkout "https://x-access-token:${token}@${host}/${GITHUB_REPOSITORY}" .'
PREP_JOB = "prep"
UV_INSTALLER = "https://astral.sh/uv/install.sh"
GITLEAKS_TARBALL = "gitleaks_8.30.1_linux_x64.tar.gz"
SETUP_SNIPPETS = (
    "apt-get",
    "uv sync",
    UV_INSTALLER,
    GITLEAKS_TARBALL,
    CLONE_CMD,
)


def _workflow_jobs(yaml: str):
    parts = re.split(r"^jobs:\s*$", yaml, maxsplit=1, flags=re.M)
    rest = parts[1] if len(parts) == 2 else ""
    return [
        (m.group(1), m.group(2))
        for m in re.finditer(
            r"^  ([A-Za-z0-9_-]+):\n([\s\S]*?)(?=^  [A-Za-z0-9_-]+:|\Z)",
            rest,
            re.M,
        )
    ]


def _precommit_hook_ids(src: str):
    return re.findall(r"^\s+- id: ([A-Za-z0-9_-]+)\s*$", src, re.M)


def _check_restore_script(job_body: str) -> str:
    match = re.search(
        r"^      - run: \|\n([\s\S]*?)(?=^      - run: |\Z)",
        job_body,
        re.M,
    )
    if match is None:
        raise AssertionError("check job is missing a restore run: | step")
    return textwrap.dedent(match.group(1))


def _gitea_artifact_handler(blob: bytes, token: str, run_id: str):
    compressed = gzip.compress(blob)
    list_path = f"/api/actions_pipeline/_apis/pipelines/workflows/{run_id}/artifacts"
    files_suffix = "/download_url"
    download_path = f"/api/actions_pipeline/_apis/pipelines/workflows/{run_id}/artifacts/99/download"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _unauthorized(self):
            self.send_response(401)
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                self._unauthorized()
                return
            if parsed.path == list_path:
                payload = {
                    "count": 1,
                    "value": [
                        {
                            "name": "prep-workspace",
                            "fileContainerResourceUrl": f"http://{self.headers.get('Host')}{list_path}/hash{files_suffix}",
                        }
                    ],
                }
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if parsed.path.endswith(files_suffix):
                query = parse_qs(parsed.query)
                if query.get("itemPath", [""])[0] != "prep-workspace":
                    self.send_response(400)
                    self.end_headers()
                    return
                payload = {
                    "value": [
                        {
                            "path": "prep-workspace/prep-workspace.tar.gz",
                            "itemType": "file",
                            "contentLocation": f"http://{self.headers.get('Host')}{download_path}",
                        }
                    ]
                }
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if parsed.path == download_path:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self.end_headers()
                self.wfile.write(compressed)
                return
            self.send_response(404)
            self.end_headers()

    return Handler


class QualityGatesTests(SimpleTestCase):
    def test_precommit_declares_five_distinct_checks(self):
        src = (ROOT / ".pre-commit-config.yaml").read_text()
        hook_ids = _precommit_hook_ids(src)
        self.assertEqual(tuple(hook_ids), CHECK_IDS)
        for hook_id, command in CHECK_COMMANDS.items():
            self.assertIn(f"id: {hook_id}", src)
            self.assertIn(command, src)
        self.assertIn("pass_filenames: false", src)
        self.assertIn("always_run: true", src)
        self.assertNotIn("pre-commit run --all-files", src)

    def test_gitea_workflow_is_actions_with_five_parallel_check_jobs(self):
        workflow_path = ROOT / ".gitea" / "workflows" / "precommit.yml"
        self.assertTrue(workflow_path.is_file())
        workflow = workflow_path.read_text()
        self.assertIsNotNone(
            re.search(r"^on:\n  push:\n  pull_request:\n", workflow, re.M),
            "workflow must trigger on push and pull_request",
        )
        self.assertIn("jobs:", workflow)
        jobs = _workflow_jobs(workflow)
        job_names = [name for name, _ in jobs]
        self.assertEqual(job_names, [PREP_JOB, *CHECK_IDS])
        self.assertNotIn("pre-commit run", workflow)
        self.assertNotIn("uses: actions/checkout", workflow)
        self.assertNotIn("actions/upload-artifact@v4", workflow)

        by_name = dict(jobs)
        prep = by_name[PREP_JOB]
        self.assertNotIn("needs:", prep, "prep must not depend on a check job")
        self.assertIsNone(re.search(r"^\s*git init\b", prep, re.M), "prep must not git init")
        self.assertIn(CLONE_CMD, prep)
        self.assertIn('git fetch --depth 1 origin "${GITHUB_SHA}"', prep)
        self.assertIn("missing job token for git fetch", prep)
        self.assertIn("apt-get", prep)
        self.assertIn(UV_INSTALLER, prep)
        self.assertIn("uv sync --frozen --all-groups", prep)
        self.assertIn(GITLEAKS_TARBALL, prep)
        self.assertIn("tar -czf /tmp/prep-workspace.tar.gz", prep)
        self.assertIn("actions/upload-artifact@v3", prep)
        self.assertIn("name: prep-workspace", prep)
        for command in CHECK_COMMANDS.values():
            self.assertNotIn(command, prep, f"prep must not run {command}")

        for name in CHECK_IDS:
            body = by_name[name]
            self.assertIn("needs: prep", body, f"{name} must wait on prep")
            needs = re.findall(r"^\s+needs:\s*(.+)\s*$", body, re.M)
            self.assertEqual(needs, ["prep"], f"{name} must needs: only prep, got {needs}")
            self.assertIn(CHECK_COMMANDS[name], body)
            self.assertIn("ACTIONS_RUNTIME_URL", body)
            self.assertIn("ACTIONS_RUNTIME_TOKEN", body)
            self.assertIn("prep-workspace.tar.gz", body)
            self.assertIn("tar -xzf", body)
            self.assertIsNone(
                re.search(r"^\s*git init\b", body, re.M),
                f"{name} must not git init",
            )
            for snippet in SETUP_SNIPPETS:
                self.assertNotIn(snippet, body, f"{name} must not repeat {snippet!r}")
            for other, command in CHECK_COMMANDS.items():
                if other == name:
                    continue
                self.assertNotIn(
                    command,
                    body,
                    f"{name} must not run {other} ({command})",
                )

        clone_jobs = [name for name, body in jobs if CLONE_CMD in body]
        self.assertEqual(clone_jobs, [PREP_JOB], "only prep clones GITHUB_SHA")

    def test_gitea_check_jobs_restore_prep_workspace_artifact(self):
        workflow = (ROOT / ".gitea" / "workflows" / "precommit.yml").read_text()
        jobs = dict(_workflow_jobs(workflow))
        restore = _check_restore_script(jobs["tests"])
        self.assertIn("ACTIONS_RUNTIME_URL", restore)
        self.assertIn("prep-workspace.tar.gz", restore)
        for name in CHECK_IDS:
            self.assertEqual(
                _check_restore_script(jobs[name]),
                restore,
                f"{name} must restore the same prep artifact as tests",
            )

        marker = "restored-from-prep\n"
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp) / "payload"
            payload.mkdir()
            (payload / "RESTORE_OK").write_text(marker)
            ci_bin = payload / ".ci" / "bin"
            ci_bin.mkdir(parents=True)
            (ci_bin / "uv").write_text("#!/bin/sh\necho uv-from-prep\n")
            (ci_bin / "gitleaks").write_text("#!/bin/sh\necho gitleaks-from-prep\n")
            tar_path = Path(tmp) / "prep-workspace.tar.gz"
            with tarfile.open(tar_path, "w:gz") as tf:
                tf.add(payload / "RESTORE_OK", arcname="RESTORE_OK")
                tf.add(ci_bin / "uv", arcname=".ci/bin/uv")
                tf.add(ci_bin / "gitleaks", arcname=".ci/bin/gitleaks")
            blob = tar_path.read_bytes()

            handler = _gitea_artifact_handler(blob, token="test-token", run_id="42")
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            work = Path(tmp) / "work"
            work.mkdir()
            gh_path = Path(tmp) / "github_path"
            env = os.environ.copy()
            env["ACTIONS_RUNTIME_URL"] = f"http://127.0.0.1:{httpd.server_address[1]}/api/actions_pipeline/"
            env["ACTIONS_RUNTIME_TOKEN"] = "test-token"
            env["GITHUB_RUN_ID"] = "42"
            env["GITHUB_PATH"] = str(gh_path)
            try:
                completed = subprocess.run(
                    ["bash", "-lc", restore],
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"restore failed: stdout={completed.stdout!r} stderr={completed.stderr!r}",
                )
                self.assertEqual((work / "RESTORE_OK").read_text(), marker)
                self.assertTrue((work / ".ci" / "bin" / "uv").is_file())
                self.assertIn(".ci/bin", gh_path.read_text())
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

    def test_readme_documents_hooks_and_five_local_checks(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("uv run pre-commit install", readme)
        self.assertIn("SKIP=tests,sast,audit,gitleaks,style git commit", readme)
        for command in CHECK_COMMANDS.values():
            self.assertIn(command, readme)
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn("bandit", pyproject)
        self.assertIn("pip-audit", pyproject)
        self.assertIn("ruff", pyproject)
        self.assertIn("pre-commit", pyproject)
        mise = (ROOT / "mise.toml").read_text()
        self.assertIn("gitleaks", mise)
