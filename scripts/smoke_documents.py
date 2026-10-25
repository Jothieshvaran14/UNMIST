"""Live smoke test for Checkpoint B: register -> login -> upload -> fetch.

Runs against a real uvicorn server on a temp SQLite DB + temp storage root.
"""

from __future__ import annotations

import io
import os
import secrets
import subprocess
import sys
import tempfile
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
    # Use the system temp directory so this script is machine-independent and
    # never embeds a developer-specific path.
    tmp = Path(tempfile.gettempdir()) / "unmist_smoke"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = "backend"
    env["DATABASE_URL"] = f"sqlite:///{tmp / 'unmist_smoke.db'}"
    env["STORAGE_LOCAL_DIR"] = str(tmp / "storage")
    # Generated per run: a test-only signing key, never a real credential.
    env["SECRET_KEY"] = secrets.token_urlsafe(48)
    env["ENVIRONMENT"] = "test"

    # The app does not create tables at startup; apply migrations to the fresh
    # temp DB so the smoke test is self-contained.
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if migrate.returncode != 0:
        print("FAIL: migration did not apply:", migrate.stderr[-2000:])
        return 1

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
        # Uploading enqueues a job, so the document is already `queued`.
        assert body["processing_state"] == "queued"
        assert body["page_count"] == 1
        assert "storage_key" not in body
        doc_id = body["id"]

        # Fetch it back.
        got = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["id"] == body["id"]
        print("fetch OK")

        # Unauthorized fetch.
        anon = client.get(f"/api/v1/documents/{doc_id}")
        assert anon.status_code == 401, anon.text
        print("unauthenticated rejected OK")

        # Original stored privately (one file so far).
        storage = Path(env["STORAGE_LOCAL_DIR"])
        orig_files = [p for p in storage.rglob("*") if p.is_file()]
        assert len(orig_files) == 1, orig_files
        print("private storage OK:", orig_files[0].name[:20] + "...")

        # Run the real worker once against the same DB + storage.
        worker = subprocess.run(
            [sys.executable, "-m", "app.workers.runner", "--once"],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert worker.returncode == 0, worker.stderr[-2000:]
        print("worker ran OK")

        # The document is now preprocessed (pages ready, no OCR claimed).
        status = client.get(f"/api/v1/documents/{doc_id}/processing", headers=headers)
        assert status.status_code == 200, status.text
        sbody = status.json()
        print(
            "processing status:",
            sbody["processing_state"],
            "pages_processed=",
            sbody["pages_processed"],
            "artifacts_ready=",
            sbody["artifacts_ready"],
        )
        assert sbody["processing_state"] == "preprocessed"
        assert sbody["pages_processed"] == 1
        assert sbody["artifacts_ready"] is True
        assert "storage_key" not in sbody
        assert "processed_storage_key" not in sbody

        # A page artifact now exists alongside the original (2 files total).
        all_files = [p for p in storage.rglob("*") if p.is_file()]
        assert len(all_files) == 2, all_files
        print("page artifact written privately OK")

        print("SMOKE TEST PASSED")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
