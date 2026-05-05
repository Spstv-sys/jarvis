"""
Senior-level unit tests for skills/skill_agent_chain.py.

Covers the 4-stage Architect→Developer→Reviewer→Refactorer pipeline with
mocked LLM responses (no network). Verifies:
  - Happy-path file generation
  - Trigger phrase stripping & empty-request guard
  - Per-stage failure short-circuits
  - Cooperative cancellation between stages
  - _call_with_retry semantics (truthy short-circuit, retry exhaustion)
  - _trim head+tail preservation and warning at boundaries
  - CODE: parsing, fenced-block fallback, refusal on garbage
  - Path-traversal protection in FILENAME
  - Safety filter blocks unsafe code (os.system / subprocess / eval / exec)
  - Markdown fence stripping
  - Graceful degradation when jarvis lacks cancel_event
"""
from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

import pytest

from skills import skill_agent_chain as sac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Reviewer must signal APPROVED to exit the self-correction loop on the
# first iteration. Tests that don't exercise multi-iteration behaviour use
# this constant in the response sequence.
_APPROVED = "STATUS: APPROVED\nCOMMENT: lgtm"


def _make_jarvis(responses, with_cancel: bool = True):
    """Build a minimal jarvis double whose llm.generate_response yields
    *responses* in order. Set ``with_cancel=False`` to omit cancel_event
    (tests graceful degradation on older instances).

    Convenience: any item equal to the sentinel string ``"<APPROVED>"`` is
    expanded to a valid Reviewer APPROVED response.
    """
    expanded = [_APPROVED if r == "<APPROVED>" else r for r in responses]
    j = MagicMock()
    j.audio = MagicMock()
    j.llm = MagicMock()
    j.llm.generate_response = MagicMock(side_effect=list(expanded))
    if with_cancel:
        j.cancel_event = threading.Event()
    else:
        # MagicMock auto-creates attrs; explicitly delete to simulate absence.
        del j.cancel_event
    return j


def _refactor_payload(filename: str = "result.py", code: str = "x = 1\n") -> str:
    return f"FILENAME: {filename}\nCODE:\n{code}"


@pytest.fixture
def tmp_sorting(tmp_path, monkeypatch):
    """Redirect SORTING_DRIVE to a per-test tmp dir."""
    monkeypatch.setattr(sac.config, "SORTING_DRIVE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Trigger / request handling
# ---------------------------------------------------------------------------
def test_empty_request_returns_prompt_message(tmp_sorting):
    j = _make_jarvis([])
    result = sac.execute("ланцюг агентів", j)
    assert "Що саме розробити" in result
    j.llm.generate_response.assert_not_called()


def test_trigger_phrase_stripped_from_request(tmp_sorting):
    payload = _refactor_payload(code="def f():\n    return 1\n")
    j = _make_jarvis(["arch", "dev", "<APPROVED>", payload])
    sac.execute("розробка агента калькулятор сум", j)
    arch_prompt = j.llm.generate_response.call_args_list[0].args[0]
    assert "калькулятор сум" in arch_prompt
    assert "розробка агента" not in arch_prompt


def test_all_triggers_stripped(tmp_sorting):
    payload = _refactor_payload(code="x=1\n")
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    cmd = "розробка агента ланцюг агентів напиши код ланцюжком агентів TASK"
    sac.execute(cmd, j)
    arch_prompt = j.llm.generate_response.call_args_list[0].args[0]
    assert "TASK" in arch_prompt
    for trig in sac.TRIGGERS:
        assert trig not in arch_prompt


# ---------------------------------------------------------------------------
# 2. Happy path
# ---------------------------------------------------------------------------
def test_happy_path_writes_file(tmp_sorting):
    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    payload = _refactor_payload("calc.py", code)
    j = _make_jarvis(["plan", "dev_code", "<APPROVED>", payload])

    result = sac.execute("ланцюг агентів калькулятор", j)

    assert "calc.py" in result
    assert j.llm.generate_response.call_count == 4
    saved = tmp_sorting / "calc.py"
    assert saved.exists()
    # Saved code is .strip()-ed by the parser. Auto-format may also reflow,
    # but for already-canonical code black is a no-op.
    assert saved.read_text(encoding="utf-8").strip() == code.strip()


def test_audio_speak_called_for_each_stage(tmp_sorting):
    payload = _refactor_payload(code="x=1\n")
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    sac.execute("ланцюг агентів задача", j)
    # Architect + Developer (iter 1) + Reviewer (iter 1) + Refactorer + final "Готово!"
    assert j.audio.speak.call_count >= 5


def test_default_filename_when_missing(tmp_sorting):
    j = _make_jarvis(["a", "d", "<APPROVED>", "CODE:\nprint('hi')\n"])
    result = sac.execute("ланцюг агентів t", j)
    assert "agent_chain_" in result
    files = list(tmp_sorting.glob("agent_chain_*.py"))
    assert len(files) == 1


# ---------------------------------------------------------------------------
# 3. Per-stage failures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stage_idx,expected_word", [
    (0, "Архітектора"),
    (1, "Розробника"),
    (2, "Рев'юера"),
    (3, "Рефакторера"),
])
def test_stage_failure_short_circuits(tmp_sorting, stage_idx, expected_word, monkeypatch):
    monkeypatch.setattr(sac.time, "sleep", lambda *_: None)  # no waits
    responses = ["plan", "dev", "<APPROVED>", _refactor_payload(code="x=1\n")]
    # Pad with extra Nones at the failing stage to exhaust retries (1 + 2 = 3).
    side_effect = []
    for i, r in enumerate(responses):
        if i == stage_idx:
            side_effect.extend([None, None, None])
        else:
            side_effect.append(_APPROVED if r == "<APPROVED>" else r)
    j = _make_jarvis(side_effect)
    result = sac.execute("ланцюг агентів задача", j)
    assert expected_word in result


# ---------------------------------------------------------------------------
# 4. Cancellation
# ---------------------------------------------------------------------------
def _cancel_after_n_calls(j, n: int):
    """Configure j.llm so cancel_event is set after the n-th call."""
    original = j.llm.generate_response.side_effect
    items = list(original)
    call_counter = {"n": 0}

    def wrapped(*args, **kwargs):
        call_counter["n"] += 1
        result = items.pop(0)
        if call_counter["n"] == n:
            j.cancel_event.set()
        return result

    j.llm.generate_response.side_effect = wrapped


@pytest.mark.parametrize("after_stage", [1, 2, 3])
def test_cancellation_between_stages(tmp_sorting, after_stage):
    payload = _refactor_payload(code="x=1\n")
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    _cancel_after_n_calls(j, after_stage)

    result = sac.execute("ланцюг агентів задача", j)

    assert "зупинено" in result.lower()
    # No file should be written when cancelled.
    assert not list(tmp_sorting.glob("*.py"))
    # LLM called exactly the cancelled stages, not later ones.
    assert j.llm.generate_response.call_count == after_stage


def test_no_cancel_event_attribute_works(tmp_sorting):
    """Older Jarvis without cancel_event must still complete normally."""
    payload = _refactor_payload(code="x=1\n")
    j = _make_jarvis(["a", "d", "<APPROVED>", payload], with_cancel=False)
    result = sac.execute("ланцюг агентів task", j)
    assert "Скрипт" in result or "agent_chain" in result or ".py" in result


# ---------------------------------------------------------------------------
# 5. _call_with_retry
# ---------------------------------------------------------------------------
def test_retry_returns_first_truthy(monkeypatch):
    monkeypatch.setattr(sac.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return "ok" if calls["n"] == 1 else "later"

    assert sac._call_with_retry(fn, retries=2) == "ok"
    assert calls["n"] == 1


def test_retry_succeeds_on_second_attempt(monkeypatch):
    monkeypatch.setattr(sac.time, "sleep", lambda *_: None)
    seq = iter([None, "ok", "extra"])
    assert sac._call_with_retry(lambda: next(seq), retries=2) == "ok"


def test_retry_exhausted_returns_last_value(monkeypatch):
    monkeypatch.setattr(sac.time, "sleep", lambda *_: None)
    seq = iter([None, None, None])
    assert sac._call_with_retry(lambda: next(seq), retries=2) is None


# ---------------------------------------------------------------------------
# 6. _trim
# ---------------------------------------------------------------------------
def test_trim_no_truncation_when_short():
    assert sac._trim("hello", max_chars=100) == "hello"


def test_trim_preserves_head_and_tail(monkeypatch):
    # Override _TAIL_CHARS so head+tail split is meaningful at max_chars=200.
    monkeypatch.setattr(sac, "_TAIL_CHARS", 50)
    head = "H" * 100
    tail = "T" * 50
    middle = "M" * 1000
    text = head + middle + tail
    out = sac._trim(text, max_chars=200)
    assert out.startswith("H")
    assert out.endswith("T" * 50)
    assert "(скорочено)" in out
    assert len(out) < len(text)


def test_trim_warns_when_tail_exceeds_max(caplog):
    # Force pathological config where _TAIL_CHARS >= max_chars.
    with caplog.at_level("WARNING"):
        out = sac._trim("X" * 100, max_chars=5)
    assert len(out) == 5
    assert any("returning tail only" in rec.message for rec in caplog.records)


def test_trim_logs_warning_on_truncation(caplog):
    with caplog.at_level("WARNING"):
        sac._trim("Y" * (sac._MAX_CONTEXT + 100))
    assert any("middle section will be dropped" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 7. Output parsing
# ---------------------------------------------------------------------------
def test_markdown_fence_stripped(tmp_sorting):
    payload = "FILENAME: m.py\nCODE:\n```python\nprint(1)\n```\n"
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j)
    saved = (tmp_sorting / "m.py").read_text(encoding="utf-8")
    assert "```" not in saved
    assert "print(1)" in saved


def test_missing_code_section_uses_fenced_fallback(tmp_sorting, caplog):
    payload = "Ось мій код:\n```python\ndef ok():\n    return 42\n```\nКінець."
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    with caplog.at_level("WARNING"):
        result = sac.execute("ланцюг агентів t", j)
    assert any("missing CODE: section" in rec.message for rec in caplog.records)
    files = list(tmp_sorting.glob("*.py"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "def ok()" in body
    assert "```" not in body


def test_missing_code_and_no_fenced_block_refuses_to_save(tmp_sorting):
    payload = "Просто текст без жодного коду."
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    result = sac.execute("ланцюг агентів t", j)
    assert "не вдалося знайти блок коду" in result or "збереження скасовано" in result
    assert not list(tmp_sorting.glob("*.py"))


def test_empty_code_section_refused(tmp_sorting):
    payload = "FILENAME: a.py\nCODE:\n   \n"
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    result = sac.execute("ланцюг агентів t", j)
    assert "не містить секцію CODE" in result or "не вдалося знайти блок коду" in result
    assert not list(tmp_sorting.glob("*.py"))


# ---------------------------------------------------------------------------
# 8. Safety / path-traversal
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad_code", [
    "import os\nos.system('rm -rf /')\n",
    "import subprocess\nsubprocess.run(['rm','x'])\n",
    "exec('print(1)')\n",
    "eval('1+1')\n",
    "__import__('os').system('x')\n",
])
def test_unsafe_code_blocked(tmp_sorting, bad_code):
    payload = f"FILENAME: bad.py\nCODE:\n{bad_code}"
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    result = sac.execute("ланцюг агентів t", j)
    assert "небезпечний" in result.lower()
    assert not (tmp_sorting / "bad.py").exists()


def test_path_traversal_filename_stripped(tmp_sorting):
    # "../escaped.py" — basename() must reduce it to "escaped.py".
    payload = "FILENAME: ../escaped.py\nCODE:\nx = 1\n"
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j)
    # Default filename used because regex rejects '/' in capture group.
    assert not (tmp_sorting.parent / "escaped.py").exists()
    files = list(tmp_sorting.glob("*.py"))
    assert len(files) == 1
    assert ".." not in files[0].name


def test_filename_with_backslash_stripped(tmp_sorting):
    payload = "FILENAME: ..\\evil.py\nCODE:\nx=1\n"
    j = _make_jarvis(["a", "d", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j)
    files = list(tmp_sorting.glob("*.py"))
    assert len(files) == 1
    assert "evil.py" not in files[0].name or files[0].parent == tmp_sorting


# ---------------------------------------------------------------------------
# 9. Self-correction loop (Developer ↔ Reviewer)
# ---------------------------------------------------------------------------
def test_review_loop_iterates_until_approved(tmp_sorting):
    """Reviewer says NEEDS_CHANGES once, then APPROVED — Developer is called twice."""
    payload = _refactor_payload(code="x = 1\n")
    responses = [
        "arch_plan",                                          # Architect
        "dev_v1",                                             # Developer iter 1
        "STATUS: NEEDS_CHANGES\nREQUIRED_CHANGES:\n- add docstring",  # Reviewer iter 1
        "dev_v2",                                             # Developer iter 2 (correction)
        _APPROVED,                                            # Reviewer iter 2
        payload,                                              # Refactorer
    ]
    j = _make_jarvis(responses)
    result = sac.execute("ланцюг агентів t", j)
    assert ".py" in result
    assert j.llm.generate_response.call_count == 6
    # The 4th call is the Developer correction — its prompt should mention feedback.
    correction_prompt = j.llm.generate_response.call_args_list[3].args[0]
    assert "add docstring" in correction_prompt
    assert "виправлену" in correction_prompt or "виправ" in correction_prompt.lower()


def test_review_loop_exhausts_max_iterations_and_proceeds(tmp_sorting, caplog):
    """If Reviewer never approves, loop bails after _MAX_REVIEW_ITERATIONS and continues to Refactorer."""
    payload = _refactor_payload(code="y = 2\n")
    responses = ["arch"]
    needs = "STATUS: NEEDS_CHANGES\nREQUIRED_CHANGES:\n- still bad"
    for _ in range(sac._MAX_REVIEW_ITERATIONS):
        responses.extend([f"dev", needs])
    responses.append(payload)  # Refactorer
    j = _make_jarvis(responses)
    with caplog.at_level("WARNING"):
        result = sac.execute("ланцюг агентів t", j)
    assert ".py" in result
    assert any("Review loop exhausted" in rec.message for rec in caplog.records)
    # 1 (arch) + 2*_MAX_REVIEW_ITERATIONS + 1 (refactor)
    assert j.llm.generate_response.call_count == 1 + 2 * sac._MAX_REVIEW_ITERATIONS + 1


# ---------------------------------------------------------------------------
# 10. AST validation & syntax retry
# ---------------------------------------------------------------------------
def test_refactorer_syntax_error_triggers_retry(tmp_sorting):
    """Broken Python from Refactorer triggers a retry; second attempt succeeds."""
    broken = _refactor_payload(code="def broken(:\n    pass\n")  # syntax error
    fixed = _refactor_payload(code="def fixed():\n    pass\n")
    j = _make_jarvis(["arch", "dev", "<APPROVED>", broken, fixed])
    result = sac.execute("ланцюг агентів t", j)
    assert ".py" in result
    files = list(tmp_sorting.glob("*.py"))
    assert len(files) == 1
    assert "def fixed()" in files[0].read_text(encoding="utf-8")
    # 1 arch + 1 dev + 1 rev + 2 refactor attempts = 5
    assert j.llm.generate_response.call_count == 5


def test_refactorer_syntax_error_exhausts_retries(tmp_sorting):
    """If all Refactorer attempts produce broken code, return error message and write nothing."""
    broken = _refactor_payload(code="def broken(:\n    pass\n")
    # 1 initial attempt + _MAX_SYNTAX_RETRIES additional attempts
    refactor_responses = [broken] * (1 + sac._MAX_SYNTAX_RETRIES)
    j = _make_jarvis(["arch", "dev", "<APPROVED>", *refactor_responses])
    result = sac.execute("ланцюг агентів t", j)
    assert "синтаксичну перевірку" in result
    assert not list(tmp_sorting.glob("*.py"))


def test_validate_syntax_helper_detects_errors():
    ok, err = sac._validate_syntax("x = 1\n")
    assert ok is True and err == ""
    ok, err = sac._validate_syntax("def x(:\n    pass\n")
    assert ok is False and err  # non-empty error string


# ---------------------------------------------------------------------------
# 11. Auto-format
# ---------------------------------------------------------------------------
def test_autoformat_returns_valid_python_when_formatters_absent(monkeypatch):
    """When ruff/black are not on PATH, _autoformat returns input unchanged."""
    monkeypatch.setattr(sac.shutil, "which", lambda _: None)
    src = "x=1\ny =2\n"
    out = sac._autoformat(src)
    assert out == src


def test_autoformat_preserves_validity(tmp_sorting):
    """Even if formatters run, output must remain syntactically valid."""
    code = "def add(a,b):return a+b\n"
    out = sac._autoformat(code)
    ok, _ = sac._validate_syntax(out)
    assert ok


# ---------------------------------------------------------------------------
# 9. Self-correction loop (Developer ↔ Reviewer)
# ---------------------------------------------------------------------------
def test_review_loop_iterates_until_approved(tmp_sorting):
    """Reviewer says NEEDS_CHANGES once, then APPROVED — Developer is called twice."""
    payload = _refactor_payload(code="x = 1\n")
    responses = [
        "arch_plan",                                          # Architect
        "dev_v1",                                             # Developer iter 1
        "STATUS: NEEDS_CHANGES\nREQUIRED_CHANGES:\n- add docstring",  # Reviewer iter 1
        "dev_v2",                                             # Developer iter 2 (correction)
        _APPROVED,                                            # Reviewer iter 2
        payload,                                              # Refactorer
    ]
    j = _make_jarvis(responses)
    result = sac.execute("ланцюг агентів t", j)
    assert ".py" in result
    assert j.llm.generate_response.call_count == 6
    # The 4th call is the Developer correction — its prompt should mention feedback.
    correction_prompt = j.llm.generate_response.call_args_list[3].args[0]
    assert "add docstring" in correction_prompt
    assert "виправлену" in correction_prompt or "виправ" in correction_prompt.lower()


def test_review_loop_exhausts_max_iterations_and_proceeds(tmp_sorting, caplog):
    """If Reviewer never approves, loop bails after _MAX_REVIEW_ITERATIONS and continues to Refactorer."""
    payload = _refactor_payload(code="y = 2\n")
    responses = ["arch"]
    needs = "STATUS: NEEDS_CHANGES\nREQUIRED_CHANGES:\n- still bad"
    for _ in range(sac._MAX_REVIEW_ITERATIONS):
        responses.extend([f"dev", needs])
    responses.append(payload)  # Refactorer
    j = _make_jarvis(responses)
    with caplog.at_level("WARNING"):
        result = sac.execute("ланцюг агентів t", j)
    assert ".py" in result
    assert any("Review loop exhausted" in rec.message for rec in caplog.records)
    # 1 (arch) + 2*_MAX_REVIEW_ITERATIONS + 1 (refactor)
    assert j.llm.generate_response.call_count == 1 + 2 * sac._MAX_REVIEW_ITERATIONS + 1


# ---------------------------------------------------------------------------
# 10. AST validation & syntax retry
# ---------------------------------------------------------------------------
def test_refactorer_syntax_error_triggers_retry(tmp_sorting):
    """Broken Python from Refactorer triggers a retry; second attempt succeeds."""
    broken = _refactor_payload(code="def broken(:\n    pass\n")  # syntax error
    fixed = _refactor_payload(code="def fixed():\n    pass\n")
    j = _make_jarvis(["arch", "dev", "<APPROVED>", broken, fixed])
    result = sac.execute("ланцюг агентів t", j)
    assert ".py" in result
    files = list(tmp_sorting.glob("*.py"))
    assert len(files) == 1
    assert "def fixed()" in files[0].read_text(encoding="utf-8")
    # 1 arch + 1 dev + 1 rev + 2 refactor attempts = 5
    assert j.llm.generate_response.call_count == 5


def test_refactorer_syntax_error_exhausts_retries(tmp_sorting):
    """If all Refactorer attempts produce broken code, return error message and write nothing."""
    broken = _refactor_payload(code="def broken(:\n    pass\n")
    # 1 initial attempt + _MAX_SYNTAX_RETRIES additional attempts
    refactor_responses = [broken] * (1 + sac._MAX_SYNTAX_RETRIES)
    j = _make_jarvis(["arch", "dev", "<APPROVED>", *refactor_responses])
    result = sac.execute("ланцюг агентів t", j)
    assert "синтаксичну перевірку" in result
    assert not list(tmp_sorting.glob("*.py"))


def test_validate_syntax_helper_detects_errors():
    ok, err = sac._validate_syntax("x = 1\n")
    assert ok is True and err == ""
    ok, err = sac._validate_syntax("def x(:\n    pass\n")
    assert ok is False and err  # non-empty error string


# ---------------------------------------------------------------------------
# 11. Auto-format
# ---------------------------------------------------------------------------
def test_autoformat_returns_valid_python_when_formatters_absent(monkeypatch):
    """When ruff/black are not on PATH, _autoformat returns input unchanged."""
    monkeypatch.setattr(sac.shutil, "which", lambda _: None)
    src = "x=1\ny =2\n"
    out = sac._autoformat(src)
    assert out == src


def test_autoformat_preserves_validity(tmp_sorting):
    """Even if formatters run, output must remain syntactically valid."""
    code = "def add(a,b):return a+b\n"
    out = sac._autoformat(code)
    ok, _ = sac._validate_syntax(out)
    assert ok


# ---------------------------------------------------------------------------
# 12. Atomic write & no-overwrite guard
# ---------------------------------------------------------------------------
def test_unique_filepath_no_collision(tmp_path):
    p = sac._unique_filepath(str(tmp_path), "x.py")
    assert p == str(tmp_path / "x.py")


def test_unique_filepath_appends_suffix_on_collision(tmp_path):
    (tmp_path / "x.py").write_text("a")
    p = sac._unique_filepath(str(tmp_path), "x.py")
    assert p == str(tmp_path / "x_2.py")
    (tmp_path / "x_2.py").write_text("b")
    p2 = sac._unique_filepath(str(tmp_path), "x.py")
    assert p2 == str(tmp_path / "x_3.py")


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "out.py"
    sac._atomic_write(str(target), "print('ok')\n")
    assert target.read_text(encoding="utf-8") == "print('ok')\n"
    # No temp leftovers
    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_atomic_write_cleans_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.py"
    target.write_text("old", encoding="utf-8")  # pre-existing

    def boom(*a, **kw):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(sac.os, "replace", boom)
    with pytest.raises(OSError):
        sac._atomic_write(str(target), "new")
    # Original file untouched, no temp file left behind.
    assert target.read_text(encoding="utf-8") == "old"
    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_no_overwrite_on_repeated_filename(tmp_sorting):
    """Two consecutive runs with the same FILENAME must produce two files."""
    payload = _refactor_payload("dup.py", "x = 1\n")
    j1 = _make_jarvis(["a", "d", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j1)

    payload2 = _refactor_payload("dup.py", "y = 2\n")
    j2 = _make_jarvis(["a", "d", "<APPROVED>", payload2])
    sac.execute("ланцюг агентів t", j2)

    files = sorted(p.name for p in tmp_sorting.glob("dup*.py"))
    assert files == ["dup.py", "dup_2.py"]
    assert (tmp_sorting / "dup.py").read_text(encoding="utf-8").strip() == "x = 1"
    assert (tmp_sorting / "dup_2.py").read_text(encoding="utf-8").strip() == "y = 2"


# ---------------------------------------------------------------------------
# 13. Ruff diagnostics injection into Reviewer prompt
# ---------------------------------------------------------------------------
def test_ruff_diagnostics_returns_empty_when_ruff_absent(monkeypatch):
    monkeypatch.setattr(sac.shutil, "which", lambda _: None)
    assert sac._ruff_diagnostics("x = 1\n") == ""


def test_ruff_diagnostics_returns_findings(monkeypatch):
    """Mock ruff to return canned diagnostics."""
    monkeypatch.setattr(sac.shutil, "which", lambda name: "/usr/bin/ruff" if name == "ruff" else None)

    class FakeProc:
        stdout = "snippet.py:1:1: F401 unused import\nsnippet.py:2:1: E501 line too long\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sac.subprocess, "run", lambda *a, **kw: FakeProc())
    out = sac._ruff_diagnostics("import os\nx = 1\n")
    assert "F401" in out
    assert "E501" in out


def test_ruff_diagnostics_caps_long_output(monkeypatch):
    monkeypatch.setattr(sac.shutil, "which", lambda name: "/usr/bin/ruff" if name == "ruff" else None)
    long_lines = "\n".join(f"snippet.py:{i}:1: E001 issue {i}" for i in range(1, 100))

    class FakeProc:
        stdout = long_lines
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sac.subprocess, "run", lambda *a, **kw: FakeProc())
    out = sac._ruff_diagnostics("x=1\n")
    assert "more)" in out
    # Should be capped near 41 lines (40 + truncation marker)
    assert len(out.splitlines()) <= 42


def test_reviewer_prompt_includes_ruff_findings(tmp_sorting, monkeypatch):
    """End-to-end: Reviewer prompt must contain ruff diagnostics when available."""
    monkeypatch.setattr(sac.shutil, "which", lambda name: "/fake/ruff" if name == "ruff" else None)

    class FakeProc:
        stdout = "snippet.py:1:1: F401 `os` imported but unused\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sac.subprocess, "run", lambda *a, **kw: FakeProc())

    dev_payload = _refactor_payload("d.py", "import os\nx = 1\n")
    final_payload = _refactor_payload("final.py", "x = 1\n")
    j = _make_jarvis(["arch", dev_payload, "<APPROVED>", final_payload])
    sac.execute("ланцюг агентів t", j)

    # Reviewer is the 3rd LLM call (index 2).
    reviewer_prompt = j.llm.generate_response.call_args_list[2].args[0]
    assert "ruff" in reviewer_prompt.lower()
    assert "F401" in reviewer_prompt


def test_reviewer_prompt_omits_ruff_section_when_ruff_absent(tmp_sorting, monkeypatch):
    monkeypatch.setattr(sac.shutil, "which", lambda _: None)
    payload = _refactor_payload("ok.py", "x = 1\n")
    j = _make_jarvis(["arch", "dev", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j)
    reviewer_prompt = j.llm.generate_response.call_args_list[2].args[0]
    assert "ruff" not in reviewer_prompt.lower()
    assert "статичного аналізатора" not in reviewer_prompt


# ---------------------------------------------------------------------------
# 12. Atomic write & no-overwrite guard
# ---------------------------------------------------------------------------
def test_unique_filepath_no_collision(tmp_path):
    p = sac._unique_filepath(str(tmp_path), "x.py")
    assert p == str(tmp_path / "x.py")


def test_unique_filepath_appends_suffix_on_collision(tmp_path):
    (tmp_path / "x.py").write_text("a")
    p = sac._unique_filepath(str(tmp_path), "x.py")
    assert p == str(tmp_path / "x_2.py")
    (tmp_path / "x_2.py").write_text("b")
    p2 = sac._unique_filepath(str(tmp_path), "x.py")
    assert p2 == str(tmp_path / "x_3.py")


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / "out.py"
    sac._atomic_write(str(target), "print('ok')\n")
    assert target.read_text(encoding="utf-8") == "print('ok')\n"
    # No temp leftovers
    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_atomic_write_cleans_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.py"
    target.write_text("old", encoding="utf-8")  # pre-existing

    def boom(*a, **kw):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(sac.os, "replace", boom)
    with pytest.raises(OSError):
        sac._atomic_write(str(target), "new")
    # Original file untouched, no temp file left behind.
    assert target.read_text(encoding="utf-8") == "old"
    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []


def test_no_overwrite_on_repeated_filename(tmp_sorting):
    """Two consecutive runs with the same FILENAME must produce two files."""
    payload = _refactor_payload("dup.py", "x = 1\n")
    j1 = _make_jarvis(["a", "d", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j1)

    payload2 = _refactor_payload("dup.py", "y = 2\n")
    j2 = _make_jarvis(["a", "d", "<APPROVED>", payload2])
    sac.execute("ланцюг агентів t", j2)

    files = sorted(p.name for p in tmp_sorting.glob("dup*.py"))
    assert files == ["dup.py", "dup_2.py"]
    assert (tmp_sorting / "dup.py").read_text(encoding="utf-8").strip() == "x = 1"
    assert (tmp_sorting / "dup_2.py").read_text(encoding="utf-8").strip() == "y = 2"


# ---------------------------------------------------------------------------
# 13. Ruff diagnostics injection into Reviewer prompt
# ---------------------------------------------------------------------------
def test_ruff_diagnostics_returns_empty_when_ruff_absent(monkeypatch):
    monkeypatch.setattr(sac.shutil, "which", lambda _: None)
    assert sac._ruff_diagnostics("x = 1\n") == ""


def test_ruff_diagnostics_returns_findings(monkeypatch):
    """Mock ruff to return canned diagnostics."""
    monkeypatch.setattr(sac.shutil, "which", lambda name: "/usr/bin/ruff" if name == "ruff" else None)

    class FakeProc:
        stdout = "snippet.py:1:1: F401 unused import\nsnippet.py:2:1: E501 line too long\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sac.subprocess, "run", lambda *a, **kw: FakeProc())
    out = sac._ruff_diagnostics("import os\nx = 1\n")
    assert "F401" in out
    assert "E501" in out


def test_ruff_diagnostics_caps_long_output(monkeypatch):
    monkeypatch.setattr(sac.shutil, "which", lambda name: "/usr/bin/ruff" if name == "ruff" else None)
    long_lines = "\n".join(f"snippet.py:{i}:1: E001 issue {i}" for i in range(1, 100))

    class FakeProc:
        stdout = long_lines
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sac.subprocess, "run", lambda *a, **kw: FakeProc())
    out = sac._ruff_diagnostics("x=1\n")
    assert "more)" in out
    # Should be capped near 41 lines (40 + truncation marker)
    assert len(out.splitlines()) <= 42


def test_reviewer_prompt_includes_ruff_findings(tmp_sorting, monkeypatch):
    """End-to-end: Reviewer prompt must contain ruff diagnostics when available."""
    monkeypatch.setattr(sac.shutil, "which", lambda name: "/fake/ruff" if name == "ruff" else None)

    class FakeProc:
        stdout = "snippet.py:1:1: F401 `os` imported but unused\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(sac.subprocess, "run", lambda *a, **kw: FakeProc())

    dev_payload = _refactor_payload("d.py", "import os\nx = 1\n")
    final_payload = _refactor_payload("final.py", "x = 1\n")
    j = _make_jarvis(["arch", dev_payload, "<APPROVED>", final_payload])
    sac.execute("ланцюг агентів t", j)

    # Reviewer is the 3rd LLM call (index 2).
    reviewer_prompt = j.llm.generate_response.call_args_list[2].args[0]
    assert "ruff" in reviewer_prompt.lower()
    assert "F401" in reviewer_prompt


def test_reviewer_prompt_omits_ruff_section_when_ruff_absent(tmp_sorting, monkeypatch):
    monkeypatch.setattr(sac.shutil, "which", lambda _: None)
    payload = _refactor_payload("ok.py", "x = 1\n")
    j = _make_jarvis(["arch", "dev", "<APPROVED>", payload])
    sac.execute("ланцюг агентів t", j)
    reviewer_prompt = j.llm.generate_response.call_args_list[2].args[0]
    assert "ruff" not in reviewer_prompt.lower()
    assert "статичного аналізатора" not in reviewer_prompt
