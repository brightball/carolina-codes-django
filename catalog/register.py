from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from urllib.parse import urlparse

from catalog import db

_register_lock = threading.Lock()
_register_started = False


def register_with_elixir(port: str | None = None) -> None:
    url = os.environ.get("CAROLINA_URL")
    token = os.environ.get("POLYGLOT_REGISTER_TOKEN")
    if not url or not token:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        print(f"register: refused scheme {parsed.scheme!r}", file=sys.stderr)
        return
    port = port or os.environ.get("PORT", "4019")
    base = os.environ.get("PUBLIC_BASE_URL", f"http://127.0.0.1:{port}")
    body = json.dumps(
        {
            **db.identity(),
            "base_url": base,
        }
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/internal/api-endpoints/register",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310
            print(f"registered with elixir: {resp.status}", file=sys.stderr)
    except Exception as exc:
        print(f"register: {exc}", file=sys.stderr)


def schedule_registration() -> None:
    """Attempt CMS registration once per process, off the listen path.

    The caller returns as soon as the background attempt is started. A slow
    or unreachable CMS must not delay the first request.
    """
    global _register_started
    with _register_lock:
        if _register_started:
            return
        _register_started = True
        threading.Thread(
            target=register_with_elixir,
            name="elixir-register",
            daemon=True,
        ).start()
