"""Юніт-тести core.thought_log.ThoughtLog: кільцевий буфер, підписки, персистенс."""
import time

from core.thought_log import ThoughtLog, STAGE_INFO, STAGE_PLAN


def test_log_and_recent():
    tl = ThoughtLog(maxlen=10)
    tl.log(STAGE_INFO, "перша")
    tl.log(STAGE_PLAN, "друга")
    rec = tl.recent()
    assert [t.text for t in rec] == ["перша", "друга"]
    assert rec[0].stage == STAGE_INFO


def test_maxlen_eviction():
    tl = ThoughtLog(maxlen=2)
    for i in range(4):
        tl.log(STAGE_INFO, f"t{i}")
    rec = tl.recent()
    assert len(rec) == 2 and [t.text for t in rec] == ["t2", "t3"]


def test_recent_stage_filter():
    tl = ThoughtLog(maxlen=10)
    tl.log(STAGE_INFO, "info")
    tl.log(STAGE_PLAN, "plan")
    only_plan = tl.recent(stages=[STAGE_PLAN])
    assert [t.text for t in only_plan] == ["plan"]


def test_subscribe_and_unsubscribe():
    tl = ThoughtLog(maxlen=10)
    seen = []
    unsub = tl.subscribe(lambda t: seen.append(t.text))
    tl.log(STAGE_INFO, "a")
    assert seen == ["a"]
    unsub()
    tl.log(STAGE_INFO, "b")
    assert seen == ["a"]  # після відписки не отримує


def test_text_truncated_to_500():
    tl = ThoughtLog(maxlen=5)
    t = tl.log(STAGE_INFO, "x" * 2000)
    assert len(t.text) == 500


def test_persistence_history(tmp_path):
    db = str(tmp_path / "thoughts.sqlite")
    tl = ThoughtLog(maxlen=50, db_path=db, preload=0)
    for i in range(3):
        tl.log(STAGE_INFO, f"подія {i}")
    # асинхронний writer — даємо час флашнутись
    hist = []
    for _ in range(20):
        hist = tl.history(limit=10)
        if len(hist) >= 3:
            break
        time.sleep(0.1)
    tl.shutdown()
    assert len(hist) >= 3


def test_stats_returns_dict():
    tl = ThoughtLog(maxlen=10)
    tl.log(STAGE_INFO, "x")
    s = tl.stats()
    assert isinstance(s, dict)
