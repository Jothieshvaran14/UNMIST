"""Live smoke test for Checkpoint B: register -> login -> upload -> fetch.

Runs against a real uvicorn server on a temp SQLite DB + temp storage root.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8123"


def make_png() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (40, 30), (20, 160, 220))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = "backend"
    env["DATABASE_URL"] = "sqlite:///C:/Users/jothi/AppData/Local/Temp/opencode/unmist_smoke.db"
    env["STORAGE_LOCAL_DIR"] = "C:/Users/jothi/AppData/Local/Temp/opencode/unmist_smoke_storage"
    env["SECRET_KEY"] = "smoke-secret-key-that-is-long-enough-0123456789abcdef"
    env["ENVIRONMENT"] = "test"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        client = httpx.Client(base_url=BASE, timeout=30.0)
        for _ in range(60):
            try:
                if client.get("/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            print("FAIL: server did not become healthy")
            return 1

        # Register + login.
        email = f"smoker-{int(time.time())}@example.com"
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "correct-horse-battery"},
        )
        assert reg.status_code == 201, reg.text
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "correct-horse-battery"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("register+login OK")

        # Upload a real PNG.
        resp = client.post(
            "/api/v1/documents/uploads",
            headers=headers,
            files={"file": ("smoke.png", make_png(), "image/png")},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        print("upload OK:", body["id"], body["processing_state"], body["media_type"])
        assert body["processing_state"] == "uploaded"
        assert body["page_count"] == 1
        assert "storage_key" not in body

        # Fetch it back.
        got = client.get(f"/api/v1/documents/{body['id']}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["id"] == body["id"]
        print("fetch OK")

        # Unauthorized fetch.
        anon = client.get(f"/api/v1/documents/{body['id']}")
        assert anon.status_code == 401, anon.text
        print("unauthenticated rejected OK")

        # Storage file exists privately.
        storage = Path(env["STORAGE_LOCAL_DIR"])
        files = [p for p in storage.rglob("*") if p.is_file()]
        assert len(files) == 1, files
        print("private storage OK:", files[0].name[:20] + "...")

        print("SMOKE TEST PASSED")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
