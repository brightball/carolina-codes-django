from __future__ import annotations

import json
import os
import sys
import urllib.request

from catalog import db


def register_with_elixir(port: str | None = None) -> None:
    url = os.environ.get("CAROLINA_URL")
    token = os.environ.get("POLYGLOT_REGISTER_TOKEN")
    if not url or not token:
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
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"registered with elixir: {resp.status}", file=sys.stderr)
    except Exception as exc:
        print(f"register: {exc}", file=sys.stderr)
