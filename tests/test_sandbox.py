"""Юніт-тести core.sandbox: політика движка (без мовчазного host-subprocess)."""
import core.sandbox as sb
from core.sandbox import run_python, status


def _set(monkeypatch, mode, allow, docker):
    monkeypatch.setattr(sb, "_MODE", mode)
    monkeypatch.setattr(sb, "_ALLOW_SUBPROCESS", allow)
    monkeypatch.setattr(sb, "_docker_available", lambda: docker)


# ----------------------------------------------- _resolve_engine
def test_disabled_mode(monkeypatch):
    _set(monkeypatch, "disabled", False, True)
    assert sb._resolve_engine() == "disabled"


def test_docker_mode_with_docker(monkeypatch):
    _set(monkeypatch, "docker", False, True)
    assert sb._resolve_engine() == "docker"


def test_docker_mode_without_docker_is_disabled(monkeypatch):
    _set(monkeypatch, "docker", False, False)
    assert sb._resolve_engine() == "disabled"


def test_auto_prefers_docker(monkeypatch):
    _set(monkeypatch, "auto", False, True)
    assert sb._resolve_engine() == "docker"


def test_auto_without_docker_refuses_by_default(monkeypatch):
    # КЛЮЧОВЕ: auto без docker і без дозволу → НЕ subprocess, а disabled
    _set(monkeypatch, "auto", False, False)
    assert sb._resolve_engine() == "disabled"


def test_auto_without_docker_allows_subprocess_when_opted_in(monkeypatch):
    _set(monkeypatch, "auto", True, False)
    assert sb._resolve_engine() == "subprocess"


def test_subprocess_mode_is_explicit_optin(monkeypatch):
    _set(monkeypatch, "subprocess", False, False)
    assert sb._resolve_engine() == "subprocess"


# ----------------------------------------------- run_python: відмова без хосту
def test_run_python_refuses_silent_host_exec(monkeypatch, tmp_path):
    _set(monkeypatch, "auto", False, False)
    script = tmp_path / "evil.py"
    script.write_text("print('pwned')\n", encoding="utf-8")
    res = run_python(str(script))
    assert res.ok is False and res.engine == "disabled"
    assert "Docker" in res.stderr and "ALLOW_SUBPROCESS" in res.stderr
    # код НЕ виконувався → нема stdout
    assert "pwned" not in res.stdout


def test_run_python_runs_when_subprocess_allowed(monkeypatch, tmp_path):
    _set(monkeypatch, "auto", True, False)   # явний opt-in
    script = tmp_path / "ok.py"
    script.write_text("print('виконано')\n", encoding="utf-8")
    res = run_python(str(script))
    assert res.engine == "subprocess" and res.ok is True
    assert "виконано" in res.stdout


def test_run_python_missing_file(monkeypatch):
    _set(monkeypatch, "auto", True, False)
    res = run_python("/no/such/script.py")
    assert res.ok is False and "not found" in res.stderr


def test_status_reports_policy(monkeypatch):
    _set(monkeypatch, "auto", False, False)
    st = status()
    assert st["allow_subprocess"] is False and st["isolated"] is False
    assert st["engine"] == "disabled"
