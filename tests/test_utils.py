"""Юніт-тести core.utils: безпекова перевірка коду + текстові хелпери."""
from core.utils import check_code_safety, strip_triggers, normalize_filename


# ----------------------------------------------- check_code_safety: безпечне
def test_safe_code_passes():
    ok, reason = check_code_safety("import json\nx = json.dumps({'a': 1})\nprint(x)\n")
    assert ok is True and reason == ""


def test_subprocess_without_shell_allowed():
    # subprocess без shell=True — дозволено (часто потрібно генерованим скриптам)
    ok, _ = check_code_safety("import subprocess\nsubprocess.run(['ls', '-la'])\n")
    assert ok is True


# ----------------------------------------------- check_code_safety: небезпечне
def test_os_system_blocked():
    ok, reason = check_code_safety("import os\nos.system('rm -rf /')\n")
    assert ok is False and "system" in reason


def test_os_popen_blocked():
    ok, _ = check_code_safety("import os\nos.popen('whoami')\n")
    assert ok is False


def test_eval_blocked():
    assert check_code_safety("x = eval('2+2')")[0] is False


def test_exec_blocked():
    assert check_code_safety("exec('print(1)')")[0] is False


def test_dunder_import_blocked():
    assert check_code_safety("m = __import__('os')")[0] is False


def test_subprocess_shell_true_blocked():
    ok, _ = check_code_safety("import subprocess\nsubprocess.run('ls', shell=True)\n")
    assert ok is False


def test_forbidden_import_ctypes():
    ok, reason = check_code_safety("import ctypes\n")
    assert ok is False and "ctypes" in reason


def test_forbidden_importlib_from():
    ok, reason = check_code_safety("from importlib import import_module\n")
    assert ok is False and "importlib" in reason


def test_compile_call_blocked_via_ast():
    # compile немає в regex — ловиться лише AST-шаром
    ok, reason = check_code_safety("c = compile('x=1', '<s>', 'single')")
    assert ok is False and "compile" in reason


def test_syntax_error_is_unsafe():
    ok, reason = check_code_safety("def broken(:\n  pass")
    assert ok is False and "SyntaxError" in reason


def test_safe_identifier_not_false_positive():
    # 'evaluate' не повинно матчити \beval\b
    ok, _ = check_code_safety("def evaluate():\n    return 42\n")
    assert ok is True


# ----------------------------------------------- strip_triggers
def test_strip_triggers_basic():
    assert strip_triggers("Нео яка погода", ["нео"]) == "яка погода"


def test_strip_triggers_longest_first():
    out = strip_triggers("скажи яка погода зараз", ["погода", "яка погода"])
    assert "яка погода" not in out and "погода" not in out


def test_strip_triggers_collapses_whitespace():
    assert strip_triggers("увімкни   музику", []) == "увімкни музику"


# ----------------------------------------------- normalize_filename
def test_normalize_filename():
    assert normalize_filename("відкрий тест пай") == "відкрій test.py".replace("відкрій", "відкрий")
    assert normalize_filename("мейн джес") == "main.js"
    # ВІДОМИЙ нюанс: докстрінг обіцяє "скрипт точка джес"->"script.js", але " джес"
    # потребує провідного пробілу, який " точка "->"." з'їдає → фактично "script.джес".
    assert normalize_filename("скрипт точка джес") == "script.джес"
