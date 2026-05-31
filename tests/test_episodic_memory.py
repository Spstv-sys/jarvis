"""Юніт-тести core.episodic_memory.EpisodicMemory: лог, recent, search (LIKE), ретенція."""
import time

from core.episodic_memory import EpisodicMemory


def _em(tmp_path):
    return EpisodicMemory(db_path=str(tmp_path / "epi.sqlite"))


def test_log_and_recent(tmp_path):
    em = _em(tmp_path)
    eid = em.log("command", "привіт світ", actor="user")
    assert eid > 0
    rec = em.recent()
    assert len(rec) == 1
    assert rec[0].kind == "command" and rec[0].text == "привіт світ" and rec[0].actor == "user"


def test_empty_text_returns_minus_one(tmp_path):
    em = _em(tmp_path)
    assert em.log("note", "") == -1


def test_recent_kinds_filter(tmp_path):
    em = _em(tmp_path)
    em.log("command", "к1")
    em.log("note", "н1")
    notes = em.recent(kinds=["note"])
    assert [e.kind for e in notes] == ["note"]


def test_search_like_fallback(tmp_path):
    em = _em(tmp_path)            # без embedder → LIKE-пошук
    em.log("command", "увімкни музику")
    em.log("command", "яка погода")
    hits = em.search("музик", top_k=5)  # підрядок "музик" є в "музику"
    assert any("музику" in e.text for e in hits)
    assert all(e.score > 0 for e in hits)


def test_delete_older_than(tmp_path):
    em = _em(tmp_path)
    em.log("command", "свіже")
    # штучно зістарюємо один запис
    old_ts = int(time.time()) - 10 * 86400
    with em._lock:
        em._conn_inst.execute("UPDATE events SET ts=? WHERE text=?", (old_ts, "свіже"))
        em._conn_inst.commit()
    deleted = em.delete_older_than(1)
    assert deleted == 1
    assert em.recent() == []


def test_stats(tmp_path):
    em = _em(tmp_path)
    em.log("command", "x")
    em.log("note", "y")
    s = em.stats()
    assert s["total"] == 2 and s["by_kind"].get("command") == 1
