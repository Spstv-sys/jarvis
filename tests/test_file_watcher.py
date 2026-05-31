"""Юніт-тести core.file_watcher.DownloadHandler: сортування за розширенням.

conftest заглушає watchdog.events як MagicMock (для headless-CI), але через
це базовий FileSystemEventHandler стає не-класом і DownloadHandler не можна
сконструювати. Тут ставимо реальний базовий клас ДО імпорту file_watcher.
"""
import sys
import types

_we = sys.modules.get("watchdog.events")
if _we is None or not isinstance(getattr(_we, "FileSystemEventHandler", None), type):
    _we = types.ModuleType("watchdog.events")
    class FileSystemEventHandler:  # реальна, успадковувана база
        pass
    _we.FileSystemEventHandler = FileSystemEventHandler
    sys.modules["watchdog.events"] = _we
    sys.modules.setdefault("watchdog", types.ModuleType("watchdog"))
    sys.modules.pop("core.file_watcher", None)  # свіжий імпорт із реальною базою

import config
from core import file_watcher
from core.file_watcher import DownloadHandler


def _handler(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SORTING_DRIVE", str(tmp_path / "drive"))
    monkeypatch.setattr(file_watcher.time, "sleep", lambda *a: None)
    return DownloadHandler()


def test_init_reads_sorting_drive(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    assert h.target_drive == str(tmp_path / "drive")


def test_sorts_pdf_to_documents(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    src = tmp_path / "report.pdf"
    src.write_text("x", encoding="utf-8")
    h._sort_file(str(src))
    assert (tmp_path / "drive" / "Documents" / "report.pdf").exists()
    assert not src.exists()


def test_sorts_by_extension_map(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    cases = {"a.png": "Images", "b.py": "Code", "c.zip": "Archives", "d.exe": "Programs"}
    for fname, folder in cases.items():
        src = tmp_path / fname
        src.write_text("x", encoding="utf-8")
        h._sort_file(str(src))
        assert (tmp_path / "drive" / folder / fname).exists(), fname


def test_unknown_extension_not_moved(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    src = tmp_path / "mystery.xyz"
    src.write_text("x", encoding="utf-8")
    h._sort_file(str(src))
    assert src.exists()


def test_move_error_is_swallowed(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    h._sort_file(str(tmp_path / "nope.pdf"))  # неіснуючий файл -> не має кидати


def test_on_created_ignores_directories(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    called = {"n": 0}
    monkeypatch.setattr(h, "_sort_file", lambda p: called.__setitem__("n", called["n"] + 1))
    ev = types.SimpleNamespace(is_directory=True, src_path=str(tmp_path / "d"))
    h.on_created(ev)
    assert called["n"] == 0


def test_on_created_spawns_sort(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(h, "_sort_file", lambda p: seen.append(p))
    ev = types.SimpleNamespace(is_directory=False, src_path=str(tmp_path / "f.pdf"))
    h.on_created(ev)
    import time as _t
    _t.sleep(0.2)
    assert seen == [str(tmp_path / "f.pdf")]
