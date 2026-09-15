import gzip
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
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
