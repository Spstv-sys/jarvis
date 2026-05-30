"""Інтеграційні тести для нового «емоційно-проактивного» стека Jarvis.

Перевіряє кінець-у-кінець:
  • Affect (текст + аудіо) → стан → промпт LLM
  • Audio features → Affect.observe_audio
  • Anticipator gating під впливом афекту (стрес/гнів/втома)
  • Reflector SCOPE_USER_TRAIT — детектори рис користувача
  • Sandbox — ізольоване виконання Python-скриптів

Тести не потребують мережі чи Docker (sandbox автоматично fallback на
subprocess з isolation flag).
"""
from __future__ import annotations

import os
import sys
import io
import time
import wave
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Affect — текст + лексика
# ---------------------------------------------------------------------------
class TestAffectText:
    def test_initial_state_is_calm(self):
        from core.affect import get_affect
        af = get_affect(); af.reset()
        st = af.snapshot()
        assert st["samples"] == 0
        assert st["valence"] == 0.0
        assert st["stress"] == 0.0
        assert st["label"] == "спокійний"

    def test_negative_text_lowers_valence(self):
        from core.affect import get_affect
        af = get_affect(); af.reset()
        af.observe_text("я ВТОМИВСЯ, нічого не виходить!!!")
        st = af.snapshot()
        assert st["valence"] < -0.05, f"очікувано негатив, got {st}"
        assert st["samples"] == 1

    def test_positive_text_raises_valence(self):
        from core.affect import get_affect
        af = get_affect(); af.reset()
        af.observe_text("дякую, чудово вийшло!")
        st = af.snapshot()
        assert st["valence"] > 0.05, f"очікувано позитив, got {st}"

    def test_prompt_block_has_state(self):
        from core.affect import get_affect
        af = get_affect(); af.reset()
        for _ in range(3):
            af.manual(valence=-0.5, arousal=0.3, stress=0.4)
        block = af.as_prompt_block()
        assert block, "prompt block має бути непорожнім"
        assert "valence" in block
        assert "stress" in block


# ---------------------------------------------------------------------------
# Audio features → Affect
# ---------------------------------------------------------------------------
def _make_wav(signal_int16: bytes, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(signal_int16)
    return buf.getvalue()


class TestAudioFeatures:
    @pytest.fixture
    def numpy_real(self):
        # numpy замокано глобально у conftest — підвантажимо справжній.
        from unittest.mock import MagicMock
        cur = sys.modules.get("numpy")
        if cur is None or isinstance(cur, MagicMock):
            sys.modules.pop("numpy", None)
            sys.modules.pop("numpy._core", None)
        import numpy as np
        if isinstance(np, MagicMock):
            pytest.skip("real numpy not installed in this env")
        return np

    def test_calm_vs_nervous_features(self, numpy_real):
        np = numpy_real
        from core.audio_features import extract_features_from_bytes

        sr = 16000
        t = np.linspace(0, 1.5, sr * 3 // 2, endpoint=False)
        calm = (0.15 * np.sin(2 * np.pi * 180 * t) * 32767).astype(np.int16)
        nervous = (
            0.35 * np.sin(2 * np.pi * (280 + 40 * np.sin(2 * np.pi * 8 * t)) * t)
            * (1 + 0.6 * np.sin(2 * np.pi * 15 * t)) * 32767
        ).astype(np.int16)

        f_calm = extract_features_from_bytes(_make_wav(calm.tobytes(), sr))
        f_nerv = extract_features_from_bytes(_make_wav(nervous.tobytes(), sr))
        assert f_calm and f_nerv

        # Нервовий звук має мати більший jitter і pitch_var.
        assert f_nerv["jitter"] > f_calm["jitter"] * 3
        assert f_nerv["pitch_var"] > f_calm["pitch_var"]
        assert f_nerv["energy"] >= f_calm["energy"] - 0.05

    def test_audio_observe_updates_affect(self, numpy_real):
        from core.affect import get_affect
        from core.audio_features import extract_features_from_bytes

        np = numpy_real
        sr = 16000
        t = np.linspace(0, 1.5, sr * 3 // 2, endpoint=False)
        nervous = (
            0.4 * np.sin(2 * np.pi * (300 + 60 * np.sin(2 * np.pi * 9 * t)) * t)
            * (1 + 0.7 * np.sin(2 * np.pi * 18 * t)) * 32767
        ).astype(np.int16)

        feats = extract_features_from_bytes(_make_wav(nervous.tobytes(), sr))
        af = get_affect(); af.reset()
        af.observe_audio(feats)
        st = af.snapshot()
        assert st["samples"] == 1
        assert st["arousal"] > 0.05


# ---------------------------------------------------------------------------
# Anticipator gating
# ---------------------------------------------------------------------------
class TestAnticipatorAffectGate:
    @pytest.fixture
    def fresh(self):
        from core.affect import get_affect
        from core.anticipator import Anticipator
        af = get_affect(); af.reset()
        a = Anticipator()
        return a, af

    def _push(self, af, n, **kw):
        for _ in range(n):
            af.manual(**kw)

    def test_no_data_passes(self, fresh):
        a, _ = fresh
        ok, shift, reason = a._affect_gate()
        assert ok is True and shift == 0 and reason == ""

    def test_high_stress_blocks(self, fresh):
        a, af = fresh
        self._push(af, 4, stress=0.9)
        ok, shift, reason = a._affect_gate()
        assert ok is False
        assert "stress" in reason

    def test_anger_blocks(self, fresh):
        a, af = fresh
        self._push(af, 4, valence=-0.7, arousal=0.85)
        ok, shift, reason = a._affect_gate()
        assert ok is False
        assert "anger" in reason

    def test_tired_only_demotes(self, fresh):
        a, af = fresh
        self._push(af, 4, valence=-0.5, arousal=0.1)
        ok, shift, reason = a._affect_gate()
        assert ok is True
        assert shift == -1

    def test_urgent_passes_through_peek_under_stress(self, fresh):
        from core.anticipator import PRIORITY_NORMAL, PRIORITY_URGENT
        a, af = fresh
        self._push(af, 5, stress=0.95)
        s_low = a._make(source="routine", priority=PRIORITY_NORMAL, text="хочеш погоду?")
        s_urg = a._make(source="system",  priority=PRIORITY_URGENT, text="батарея 5%!")
        a._pending = [s_low, s_urg]
        peeked = a.peek()
        assert peeked is not None
        assert peeked.priority == PRIORITY_URGENT


# ---------------------------------------------------------------------------
# Reflector SCOPE_USER_TRAIT
# ---------------------------------------------------------------------------
class TestReflectorUserTraits:
    def _mk_eps(self, texts, base_ts: int):
        from types import SimpleNamespace
        eps = []
        for i, t in enumerate(texts):
            eps.append(SimpleNamespace(
                id=0, ts=base_ts + 60 * i,
                kind="command", actor="user", text=t, meta={},
            ))
        return eps

    def test_detects_terse_and_evening(self):
        from core.reflector import get_reflector, SCOPE_USER_TRAIT
        from core.affect import get_affect
        get_affect().reset()

        evening = int(time.mktime(time.strptime(
            "2026-05-10 21:00:00", "%Y-%m-%d %H:%M:%S")))
        eps = self._mk_eps(
            ["запусти код", "покажи тест", "новий код",
             "ще тест", "код глянь", "тест запусти", "перевір код"],
            evening,
        )
        lessons = get_reflector()._detect_user_traits(eps)
        traits = {l.meta.get("trait") for l in lessons}
        assert all(l.scope == SCOPE_USER_TRAIT for l in lessons)
        assert "active_period" in traits
        assert "terse" in traits

    def test_detects_baseline_low_from_affect(self):
        from core.reflector import get_reflector
        from core.affect import get_affect
        af = get_affect(); af.reset()
        for _ in range(10):
            af.manual(valence=-0.6)

        eps = self._mk_eps(
            ["що там", "як справи", "розкажи новину",
             "глянь пошту", "пошук статтей"],
            int(time.time()) - 3600,
        )
        lessons = get_reflector()._detect_user_traits(eps)
        traits = {l.meta.get("trait") for l in lessons}
        assert "baseline_low" in traits


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------
class TestSandbox:
    @pytest.fixture
    def script_factory(self, tmp_path):
        def _make(body: str) -> str:
            p = tmp_path / f"s_{int(time.time()*1000)}.py"
            p.write_text(body, encoding="utf-8")
            return str(p)
        return _make

    def test_status_reports_engine(self):
        from core.sandbox import status
        s = status()
        assert s["engine"] in {"docker", "subprocess", "disabled"}
        assert s["mode"] in {"auto", "docker", "subprocess", "disabled"}

    def test_runs_simple_script(self, script_factory):
        from core.sandbox import run_python
        path = script_factory("print('hi')\n")
        res = run_python(path, timeout=10)
        assert res.ok is True
        assert res.returncode == 0
        assert "hi" in res.stdout
        assert res.timed_out is False

    def test_propagates_stderr_on_error(self, script_factory):
        from core.sandbox import run_python
        path = script_factory("raise ValueError('boom')\n")
        res = run_python(path, timeout=10)
        assert res.ok is False
        assert res.returncode != 0
        assert "boom" in res.stderr or "ValueError" in res.stderr

    def test_timeout_kills_long_script(self, script_factory):
        from core.sandbox import run_python
        path = script_factory(
            "import time\n"
            "for _ in range(60):\n"
            "    time.sleep(1)\n"
        )
        t0 = time.time()
        res = run_python(path, timeout=2)
        assert res.timed_out is True
        assert res.ok is False
        # Не повинен зайняти набагато довше за timeout (+5s допуск на docker).
        assert time.time() - t0 < 12

    def test_missing_file_returns_error(self):
        from core.sandbox import run_python
        res = run_python("/nonexistent/__nope__.py")
        assert res.ok is False
        assert "not found" in res.stderr


# ---------------------------------------------------------------------------
# End-to-end: афект впливає на промпт LLM, який потрапляє в anticipator
# ---------------------------------------------------------------------------
class TestEndToEndStressLoop:
    def test_stress_mutes_anticipator_and_appears_in_prompt(self):
        """Стресовий стан → у промпті є секція АФЕКТ + Anticipator мовчить."""
        from core.affect import get_affect
        from core.anticipator import Anticipator, PRIORITY_NORMAL

        af = get_affect(); af.reset()
        for _ in range(5):
            af.manual(stress=0.85, valence=-0.4, note="дедлайн")

        # 1) Стан у промпті
        block = af.as_prompt_block()
        assert "stress" in block
        assert "напружений" in block.lower() or "роздратован" in block.lower()

        # 2) Anticipator блокує
        ant = Anticipator()
        ok, _, reason = ant._affect_gate()
        assert ok is False
        assert "stress" in reason

        # 3) Не-URGENT пропозиція не показується
        s_low = ant._make(source="routine", priority=PRIORITY_NORMAL,
                          text="спробуй цю книгу")
        ant._pending = [s_low]
        assert ant.peek() is None
