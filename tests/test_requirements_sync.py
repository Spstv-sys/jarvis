"""Drift-guard: requirements*.txt не повинні розходитися з pyproject.toml.

pyproject.toml — канонічне джерело залежностей. Цей тест ловить ситуацію,
коли пакет додано в requirements.txt / requirements.server.txt, але забуто в
pyproject (або навпаки прибрано з pyproject). Версії не звіряються — лише
склад за іменами (нормалізовано за PEP 503).

requirements-train.txt НЕ перевіряється: це окремий набір для тренування
голосових моделей, поза рантайм-залежностями застосунку.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _norm(name: str) -> str:
    # PEP 503: регістр + [-_.] → '-'
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _req_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # відсікаємо специфікатори версій / extras / маркери
        pkg = re.split(r"[<>=!~;\[ ]", line, 1)[0]
        if pkg:
            names.add(_norm(pkg))
    return names


def _pyproject_names() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        try:
            import tomllib  # py3.11+
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore
        data = tomllib.loads(text)
        proj = data.get("project", {})
        deps = list(proj.get("dependencies", []))
        for grp in (proj.get("optional-dependencies", {}) or {}).values():
            deps += list(grp)
        return {_norm(re.split(r"[<>=!~;\[ ]", d, 1)[0]) for d in deps if d}
    except Exception:
        # Фолбек без TOML-парсера: тягнемо лапкові рядки-залежності.
        names = set()
        for m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:[<>=!~\[].*?)?"', text):
            names.add(_norm(m.group(1)))
        return names


def test_requirements_txt_subset_of_pyproject():
    missing = _req_names(ROOT / "requirements.txt") - _pyproject_names()
    assert not missing, f"у requirements.txt є пакети, відсутні в pyproject: {sorted(missing)}"


def test_requirements_server_subset_of_pyproject():
    missing = _req_names(ROOT / "requirements.server.txt") - _pyproject_names()
    assert not missing, f"у requirements.server.txt є пакети, відсутні в pyproject: {sorted(missing)}"


def test_pyproject_has_dependencies():
    assert len(_pyproject_names()) >= 20  # санітарна перевірка, що парсинг працює
