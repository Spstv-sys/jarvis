"""Ізольоване виконання Python-скриптів — «embodied AI» з безпекою.

Коли в системі є Docker, скрипт запускається у контейнері з:
  • вимкненою мережею            (--network=none)
  • read-only кореневою ФС       (--read-only)
  • tmpfs у /tmp                 (--tmpfs /tmp:size=64m)
  • лімітом RAM 256 MiB          (--memory)
  • лімітом 1 CPU                (--cpus)
  • non-root користувачем        (--user 65534:65534)
  • заборонено отримання нових прав (--security-opt no-new-privileges)
  • mount тільки скрипта read-only (-v /host/script.py:/sandbox/run.py:ro)
  • жорстким таймаутом (--stop-timeout + watchdog)

Якщо Docker недоступний — fallback у `subprocess.run()` з таймаутом
і попередженням у ThoughtLog (бо це менш безпечно).

Публічне API:
    from core.sandbox import run_python, SandboxResult
    res = run_python("/path/to/script.py", timeout=15)
    res.ok, res.stdout, res.stderr, res.returncode, res.engine

Конфіг (env / config.py):
    JARVIS_SANDBOX           — auto|docker|subprocess|disabled (default auto)
    JARVIS_SANDBOX_IMAGE     — docker-образ (default python:3.11-slim)
    JARVIS_SANDBOX_MEM       — '256m' за замовчуванням
    JARVIS_SANDBOX_CPUS      — '1.0' за замовчуванням
    JARVIS_SANDBOX_TIMEOUT   — fallback timeout (default 15)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def _cfg(key: str, default):
    """Прочитати env-змінну або поле з config.py (env має пріоритет)."""
    val = os.getenv(key)
    if val is not None and val != "":
        return val
    try:
        import config  # noqa: WPS433
        return getattr(config, key, default)
    except Exception:
        return default


_MODE        = str(_cfg("JARVIS_SANDBOX", "auto")).lower()
_IMAGE       = str(_cfg("JARVIS_SANDBOX_IMAGE", "python:3.11-slim"))
_MEM         = str(_cfg("JARVIS_SANDBOX_MEM", "256m"))
_CPUS        = str(_cfg("JARVIS_SANDBOX_CPUS", "1.0"))
_DEF_TIMEOUT = int(_cfg("JARVIS_SANDBOX_TIMEOUT", 15))


@dataclass
class SandboxResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    engine: str          # 'docker' | 'subprocess' | 'disabled'
    duration_sec: float
    timed_out: bool = False


# ---------------------------------------------------------------------------
_DOCKER_OK: Optional[bool] = None


def _docker_available() -> bool:
    """Перевірити, чи Docker встановлений і відповідає (cached)."""
    global _DOCKER_OK
    if _DOCKER_OK is not None:
        return _DOCKER_OK
    if not shutil.which("docker"):
        _DOCKER_OK = False
        return False
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=4,
        )
        _DOCKER_OK = (proc.returncode == 0 and bool(proc.stdout.strip()))
    except (subprocess.TimeoutExpired, OSError):
        _DOCKER_OK = False
    return _DOCKER_OK


def _resolve_engine() -> str:
    """Визначити фактичний движок з урахуванням режиму та доступності Docker."""
    if _MODE == "disabled":
        return "disabled"
    if _MODE == "docker":
        return "docker" if _docker_available() else "disabled"
    if _MODE == "subprocess":
        return "subprocess"
    # auto:
    return "docker" if _docker_available() else "subprocess"


def _think(stage: str, msg: str, **fields) -> None:
    try:
        from core.thought_log import think
        think(stage, msg, **fields)
    except Exception:
        pass


# ---------------------------------------------------------------------------
def run_python(
    script_path: str,
    *,
    args: Optional[list] = None,
    timeout: Optional[int] = None,
    stdin_text: Optional[str] = None,
) -> SandboxResult:
    """Запустити *script_path* у пісочниці. Завжди повертає `SandboxResult`."""
    timeout = int(timeout or _DEF_TIMEOUT)
    args = list(args or [])
    if not os.path.isfile(script_path):
        return SandboxResult(
            ok=False, returncode=-1, stdout="", stderr=f"script not found: {script_path}",
            engine="disabled", duration_sec=0.0,
        )

    engine = _resolve_engine()
    started = time.time()

    if engine == "disabled":
        _think("info", f"sandbox: відмова виконати {os.path.basename(script_path)} — режим disabled",
               scope="sandbox")
        return SandboxResult(
            ok=False, returncode=-1, stdout="",
            stderr="sandbox disabled by config (JARVIS_SANDBOX=disabled)",
            engine="disabled", duration_sec=0.0,
        )

    if engine == "docker":
        try:
            return _run_docker(script_path, args, timeout, stdin_text, started)
        except Exception as e:
            logger.warning("[Sandbox] docker run failed, fallback to subprocess: %s", e)
            _think("info", f"sandbox: docker провалився ({e}) → fallback subprocess",
                   scope="sandbox")
            engine = "subprocess"

    return _run_subprocess(script_path, args, timeout, stdin_text, started)


# ---------------------------------------------------------------------------
def _run_docker(
    script_path: str, args: list, timeout: int,
    stdin_text: Optional[str], started: float,
) -> SandboxResult:
    abs_path = os.path.abspath(script_path)
    name = f"jarvis-sb-{int(time.time()*1000)}"
    cmd = [
        "docker", "run", "--rm",
        "--name", name,
        "--network=none",
        "--read-only",
        "--tmpfs", "/tmp:size=64m,mode=1777",
        f"--memory={_MEM}",
        f"--cpus={_CPUS}",
        "--user", "65534:65534",
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--pids-limit", "128",
        "--workdir", "/sandbox",
        "-v", f"{abs_path}:/sandbox/run.py:ro",
    ]
    if stdin_text is not None:
        cmd.append("-i")
    cmd += [_IMAGE, "python", "-I", "/sandbox/run.py", *args]

    timed_out = False
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            input=stdin_text, timeout=timeout + 5,  # +5 для запуску контейнера
            encoding="utf-8", errors="replace",
        )
        rc = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        # Жорстко прибиваємо контейнер.
        try:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=5)
        except Exception:
            pass
        rc = -9
        out = (e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) or ""
        err = (e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")) + \
              f"\n[sandbox] timeout after {timeout}s"
    dur = time.time() - started
    _think("info",
           f"sandbox/docker: {os.path.basename(script_path)} rc={rc} t={dur:.1f}s",
           scope="sandbox", engine="docker", rc=rc, timed_out=timed_out)
    return SandboxResult(
        ok=(rc == 0 and not timed_out),
        returncode=rc, stdout=out, stderr=err,
        engine="docker", duration_sec=dur, timed_out=timed_out,
    )


# ---------------------------------------------------------------------------
def _run_subprocess(
    script_path: str, args: list, timeout: int,
    stdin_text: Optional[str], started: float,
) -> SandboxResult:
    timed_out = False
    cmd = [sys.executable, "-I", script_path, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            input=stdin_text, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        rc = -9
        out = (e.stdout if isinstance(e.stdout, str) else "") or ""
        err = (e.stderr if isinstance(e.stderr, str) else "") + \
              f"\n[sandbox] timeout after {timeout}s"
    dur = time.time() - started
    _think("info",
           f"sandbox/subprocess: {os.path.basename(script_path)} rc={rc} t={dur:.1f}s",
           scope="sandbox", engine="subprocess", rc=rc, timed_out=timed_out)
    return SandboxResult(
        ok=(rc == 0 and not timed_out),
        returncode=rc, stdout=out, stderr=err,
        engine="subprocess", duration_sec=dur, timed_out=timed_out,
    )


# ---------------------------------------------------------------------------
def status() -> dict:
    """Повертає поточний стан пісочниці — для UI / діагностики."""
    return {
        "mode":            _MODE,
        "engine":          _resolve_engine(),
        "docker_present":  _docker_available(),
        "image":           _IMAGE,
        "memory_limit":    _MEM,
        "cpu_limit":       _CPUS,
        "default_timeout": _DEF_TIMEOUT,
    }
