"""Isolated execution of learner-submitted code.

This is a *baseline* isolation boundary, not a hardened sandbox. It runs code in
a separate process with resource limits and no inherited environment. A
production deployment must additionally run this inside a container or a
gVisor/Firecracker-style sandbox with no network access and dropped
capabilities. The API process itself never executes learner code.

Security notes:
  * The child runs with ``-I`` (isolated) and a stripped environment.
  * CPU time, address space, file count, and process count are limited on
    POSIX via ``setrlimit``.
  * Wall-clock timeouts are enforced by the parent.
  * Output size is bounded.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    rejected: bool = False
    rejection_reason: str | None = None


_REJECTED_SUBSTRINGS = (
    "__import__(",
    "importlib",
    "ctypes",
    "socket",
    "subprocess",
    "os.system",
    "os.popen",
    "eval(",
    "exec(",
    "compile(",
)


def _posix_limits(cpu_seconds: int, memory_mb: int):
    import resource

    def apply() -> None:  # pragma: no cover - executed in the child
        # ``resource`` is POSIX-only; typeshed has no stubs for it on Windows.
        resource.setrlimit(  # type: ignore[attr-defined]
            resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1)  # type: ignore[attr-defined]
        )
        resource.setrlimit(  # type: ignore[attr-defined]
            resource.RLIMIT_AS,  # type: ignore[attr-defined]
            (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024),
        )
        resource.setrlimit(  # type: ignore[attr-defined]
            resource.RLIMIT_NOFILE, (32, 32)  # type: ignore[attr-defined]
        )
        resource.setrlimit(  # type: ignore[attr-defined]
            resource.RLIMIT_NPROC, (16, 16)  # type: ignore[attr-defined]
        )

    return apply


def run_python(
    code: str,
    *,
    timeout_seconds: float = 2.0,
    max_output_bytes: int = 64 * 1024,
    memory_mb: int = 256,
    cpu_seconds: int = 2,
) -> SandboxResult:
    """Execute ``code`` in a subprocess with resource limits.

    A lightweight static rejection pass blocks obvious sandbox-escape attempts.
    This is defense in depth, not a substitute for OS-level isolation.
    """
    lowered = code.lower()
    for marker in _REJECTED_SUBSTRINGS:
        if marker in lowered:
            return SandboxResult(
                stdout="",
                stderr="Code was rejected by the execution policy.",
                exit_code=-1,
                timed_out=False,
                rejected=True,
                rejection_reason=f"blocked construct: {marker}",
            )

    with tempfile.TemporaryDirectory(prefix="unmist-sandbox-") as workdir:
        script = Path(workdir) / "submission.py"
        script.write_text(code, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
        }
        if os.name == "nt":
            # Windows needs a few system variables to bootstrap the interpreter.
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "COMSPEC"):
                value = os.environ.get(key)
                if value:
                    env[key] = value
        else:
            env["HOME"] = workdir
        command = [sys.executable, "-I", str(script)]
        kwargs: dict = dict(
            cwd=workdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            text=True,
            errors="replace",
        )
        if os.name == "posix":
            kwargs["preexec_fn"] = _posix_limits(cpu_seconds, memory_mb)
        try:
            completed = subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                stdout=(exc.stdout or "")[:max_output_bytes] if isinstance(exc.stdout, str) else "",
                stderr="Execution timed out.",
                exit_code=-1,
                timed_out=True,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return SandboxResult(
                stdout="",
                stderr=f"Sandbox error: {type(exc).__name__}",
                exit_code=-1,
                timed_out=False,
            )

        return SandboxResult(
            stdout=(completed.stdout or "")[:max_output_bytes],
            stderr=(completed.stderr or "")[:max_output_bytes],
            exit_code=completed.returncode,
            timed_out=False,
        )
