"""Affect — Шар емоційного стану користувача (Theory of Mind, базовий рівень).

Тримає поточну оцінку афекту користувача за трьома осями:

  • valence  ∈ [-1..+1]  — настрій (негативний ↔ позитивний)
  • arousal  ∈ [ 0..+1]  — енергія / збудження
  • stress   ∈ [ 0..+1]  — рівень напруги / тривоги

Стан оновлюється з двох незалежних каналів:

  1) `observe_text(text)` — лексиконний sentiment (UA+EN, без важких моделей).
     Працює завжди, нульові залежності. За бажанням можна підставити свій
     класифікатор через `set_text_scorer(callable)`.

  2) `observe_audio(features)` — приймає словник акустичних фіч (pitch_var,
     speech_rate, energy, jitter, ...). Сам не рахує — його викликає той,
     хто має доступ до WAV (jarvis_media / Whisper-пайплайн). Вбудовані
     евристики транслюють фічі у valence/arousal/stress.

Стан — це експоненційно згладжений середній за останніми N спостереженнями
(EMA) → стійкий до одиничних викидів, але швидко реагує на стійку зміну.

Використання:

    from core.affect import get_affect, STAGE_AFFECT
    a = get_affect()
    a.observe_text("я втомився, нічого не виходить")
    a.observe_audio({"speech_rate": 0.4, "pitch_var": 0.2, "energy": 0.3})
    block = a.as_prompt_block()        # рядок для системного промпту LLM
    snap  = a.snapshot()               # dict — для UI/діагностики

Підмішується у промпт через llm_handler (поряд з user_profile).
Логує зміни в ThoughtLog зі стадією STAGE_AFFECT.
"""
from __future__ import annotations

import re
import time
import logging
import threading
from collections import deque
from dataclasses import dataclass, asdict
from typing import Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Окрема стадія в ThoughtLog — щоб UI міг фільтрувати "поток емоцій".
STAGE_AFFECT = "affect"

# ---------------------------------------------------------------------------
# Лексикон (мінімальний, UA + EN). Розширюється через add_lexicon().
# Вага: позитив > 0, негатив < 0; |w| масштаб впливу на valence.
# ---------------------------------------------------------------------------
_POS_UA = {
    "дякую": 0.6, "клас": 0.7, "круто": 0.7, "чудово": 0.8, "супер": 0.7,
    "люблю": 0.8, "радий": 0.7, "рада": 0.7, "вдячний": 0.7, "вдячна": 0.7,
    "добре": 0.5, "класно": 0.6, "приємно": 0.6, "вийшло": 0.5, "успіх": 0.7,
    "геніально": 0.8, "ідеально": 0.7, "сподобалось": 0.6, "молодець": 0.7,
}
_NEG_UA = {
    "погано": -0.6, "жахливо": -0.9, "ненавиджу": -0.9, "втомився": -0.6,
    "втомилась": -0.6, "втомлений": -0.6, "втомлена": -0.6, "злий": -0.7,
    "зла": -0.7, "бісить": -0.8, "сумно": -0.6, "сумний": -0.6, "сумна": -0.6,
    "не виходить": -0.6, "помилка": -0.4, "не працює": -0.5, "дурний": -0.6,
    "тупий": -0.6, "ідіот": -0.9, "блін": -0.3, "капець": -0.5, "жесть": -0.5,
    "набридло": -0.7, "не можу": -0.4, "складно": -0.3, "важко": -0.3,
    "проблема": -0.3, "провал": -0.7, "розчарований": -0.7, "тривога": -0.6,
    "страшно": -0.6, "боюсь": -0.6, "болить": -0.5,
}
_POS_EN = {
    "thanks": 0.6, "great": 0.7, "awesome": 0.8, "love": 0.8, "good": 0.4,
    "cool": 0.6, "nice": 0.5, "perfect": 0.7, "happy": 0.7, "excellent": 0.8,
    "works": 0.4, "fixed": 0.5,
}
_NEG_EN = {
    "bad": -0.5, "terrible": -0.8, "hate": -0.9, "tired": -0.6, "angry": -0.7,
    "sad": -0.6, "broken": -0.5, "fail": -0.6, "error": -0.3, "stuck": -0.5,
    "annoying": -0.6, "stupid": -0.6, "shit": -0.7, "fuck": -0.7,
}
# Стрес-маркери (підвищують stress сильніше за valence).
_STRESS = {
    "терміново": 0.7, "швидко": 0.3, "негайно": 0.8, "asap": 0.7, "urgent": 0.7,
    "горить": 0.6, "дедлайн": 0.6, "deadline": 0.6, "паніка": 0.9, "panic": 0.9,
    "не встигаю": 0.7, "пізно": 0.4,
}

_LEX_POS: Dict[str, float] = {**_POS_UA, **_POS_EN}
_LEX_NEG: Dict[str, float] = {**_NEG_UA, **_NEG_EN}
_LEX_STRESS: Dict[str, float] = dict(_STRESS)


# ---------------------------------------------------------------------------
# Дані
# ---------------------------------------------------------------------------
@dataclass
class AffectSample:
    ts: float
    valence: float = 0.0     # [-1..+1]
    arousal: float = 0.0     # [ 0..+1]
    stress:  float = 0.0     # [ 0..+1]
    source:  str = "text"    # "text" | "audio" | "manual"
    weight:  float = 1.0     # внесок у EMA (1.0 — звичайний)
    note:    str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AffectState:
    """Поточний згладжений стан користувача."""
    valence: float = 0.0
    arousal: float = 0.0
    stress:  float = 0.0
    samples: int = 0
    updated: float = 0.0

    def label(self) -> str:
        """Один з: 'спокійний', 'позитивний', 'втомлений', 'роздратований',
        'збуджений', 'напружений', 'нейтральний'. Для as_prompt_block."""
        if self.stress >= 0.55:
            return "напружений"
        if self.valence <= -0.35 and self.arousal >= 0.5:
            return "роздратований"
        if self.valence <= -0.35:
            return "втомлений"
        if self.valence >= 0.4 and self.arousal >= 0.5:
            return "збуджений"
        if self.valence >= 0.4:
            return "позитивний"
        if self.arousal <= 0.25 and abs(self.valence) < 0.3:
            return "спокійний"
        return "нейтральний"

    def to_dict(self) -> dict:
        return {
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "stress":  round(self.stress, 3),
            "label":   self.label(),
            "samples": self.samples,
            "updated": self.updated,
        }


# ---------------------------------------------------------------------------
# Affect engine
# ---------------------------------------------------------------------------
TextScorer = Callable[[str], Tuple[float, float, float]]
"""Кастомний скорер тексту: повертає (valence, arousal, stress)."""

AudioScorer = Callable[[Dict[str, float]], Tuple[float, float, float]]
"""Кастомний скорер аудіо-фіч."""


class Affect:
    """Singleton-ish стан афекту. Потокобезпечний, без важких залежностей."""

    def __init__(
        self,
        ema_alpha: float = 0.35,        # швидкість адаптації EMA (0.2 повільно, 0.5 жваво)
        history_max: int = 200,
        decay_half_life_sec: float = 600.0,  # без нових подій — повертаємось до нейтрального за ~10 хв
    ):
        self._lock = threading.RLock()
        self._state = AffectState(updated=time.time())
        self._history: Deque[AffectSample] = deque(maxlen=history_max)
        self._alpha = float(ema_alpha)
        self._decay_t = float(decay_half_life_sec)
        self._text_scorer: Optional[TextScorer] = None
        self._audio_scorer: Optional[AudioScorer] = None
        self._subs: List[Callable[[AffectState], None]] = []

    # ---------- API: customization ----------
    def set_text_scorer(self, fn: Optional[TextScorer]) -> None:
        with self._lock:
            self._text_scorer = fn

    def set_audio_scorer(self, fn: Optional[AudioScorer]) -> None:
        with self._lock:
            self._audio_scorer = fn

    def add_lexicon(self, *, positive: Optional[Dict[str, float]] = None,
                    negative: Optional[Dict[str, float]] = None,
                    stress: Optional[Dict[str, float]] = None) -> None:
        if positive: _LEX_POS.update(positive)
        if negative: _LEX_NEG.update(negative)
        if stress:   _LEX_STRESS.update(stress)

    def subscribe(self, cb: Callable[[AffectState], None]) -> None:
        with self._lock:
            self._subs.append(cb)

    # ---------- API: observers ----------
    def observe_text(self, text: str, *, weight: float = 1.0) -> AffectSample:
        text = (text or "").strip()
        if not text:
            return AffectSample(ts=time.time(), source="text", weight=0.0)
        if self._text_scorer is not None:
            try:
                v, a, s = self._text_scorer(text)
            except Exception as e:
                logger.debug(f"[Affect] custom text_scorer failed: {e}")
                v, a, s = self._lexicon_score(text)
        else:
            v, a, s = self._lexicon_score(text)
        sample = AffectSample(
            ts=time.time(), valence=v, arousal=a, stress=s,
            source="text", weight=weight, note=text[:80],
        )
        self._absorb(sample)
        return sample

    def observe_audio(self, features: Dict[str, float], *, weight: float = 1.0) -> AffectSample:
        """features: довільний dict акустичних метрик. Очікувані ключі:
            pitch_var    — нормалізована дисперсія F0 (0..1)
            speech_rate  — слів/сек, нормалізовано (0..1, ~0.5 норма)
            energy       — RMS енергія голосу (0..1)
            jitter       — мікротремор (0..1, високий → стрес)
        Будь-який ключ опційний; невідомі ігноруються.
        """
        if not features:
            return AffectSample(ts=time.time(), source="audio", weight=0.0)
        if self._audio_scorer is not None:
            try:
                v, a, s = self._audio_scorer(features)
            except Exception as e:
                logger.debug(f"[Affect] custom audio_scorer failed: {e}")
                v, a, s = self._heuristic_audio(features)
        else:
            v, a, s = self._heuristic_audio(features)
        sample = AffectSample(
            ts=time.time(), valence=v, arousal=a, stress=s,
            source="audio", weight=weight,
            note=",".join(f"{k}={round(float(v_), 2)}" for k, v_ in features.items())[:80],
        )
        self._absorb(sample)
        return sample

    def manual(self, *, valence: float = 0.0, arousal: float = 0.0,
               stress: float = 0.0, note: str = "") -> AffectSample:
        sample = AffectSample(
            ts=time.time(),
            valence=_clip(valence, -1, 1),
            arousal=_clip(arousal, 0, 1),
            stress=_clip(stress, 0, 1),
            source="manual", note=note[:80],
        )
        self._absorb(sample)
        return sample

    # ---------- API: read ----------
    def snapshot(self) -> dict:
        with self._lock:
            self._apply_decay_locked()
            return self._state.to_dict()

    def state(self) -> AffectState:
        with self._lock:
            self._apply_decay_locked()
            return AffectState(**asdict(self._state))

    def history(self, n: int = 50) -> List[dict]:
        with self._lock:
            items = list(self._history)[-n:]
        return [s.to_dict() for s in items]

    def reset(self) -> None:
        with self._lock:
            self._state = AffectState(updated=time.time())
            self._history.clear()

    def as_prompt_block(self) -> str:
        """Короткий рядок для системного промпту LLM. '' якщо ще нема даних."""
        with self._lock:
            self._apply_decay_locked()
            st = self._state
            samples = st.samples
        if samples == 0:
            return ""
        label = st.label()
        # Тон-підказка для моделі — конкретна поведінка, а не загальне «настрій сумний».
        hint = _BEHAVIOR_HINTS.get(label, "")
        parts = [f"Емоційний стан користувача: {label}"]
        parts.append(
            f"(valence={st.valence:+.2f}, arousal={st.arousal:.2f}, stress={st.stress:.2f})"
        )
        if hint:
            parts.append(hint)
        return " ".join(parts)

    # ---------- internals ----------
    def _absorb(self, sample: AffectSample) -> None:
        if sample.weight <= 0:
            return
        with self._lock:
            self._apply_decay_locked()
            a = _clip(self._alpha * sample.weight, 0.01, 0.95)
            st = self._state
            st.valence = (1 - a) * st.valence + a * _clip(sample.valence, -1, 1)
            st.arousal = (1 - a) * st.arousal + a * _clip(sample.arousal, 0, 1)
            st.stress  = (1 - a) * st.stress  + a * _clip(sample.stress, 0, 1)
            st.samples += 1
            st.updated = sample.ts
            self._history.append(sample)
            snap = AffectState(**asdict(st))
            subs = list(self._subs)
        # Сповіщаємо ThoughtLog (поза замком).
        try:
            from core.thought_log import think
            think(STAGE_AFFECT,
                  f"{snap.label()} v={snap.valence:+.2f} a={snap.arousal:.2f} s={snap.stress:.2f}"
                  f" ({sample.source})",
                  source=sample.source, note=sample.note)
        except Exception:
            pass
        for cb in subs:
            try:
                cb(snap)
            except Exception as e:
                logger.debug(f"[Affect] subscriber error: {e}")

    def _apply_decay_locked(self) -> None:
        """Експоненційне згасання до нейтрального стану, якщо немає нових подій."""
        if self._state.samples == 0 or self._decay_t <= 0:
            return
        dt = time.time() - self._state.updated
        if dt <= 0:
            return
        # half-life => factor = 0.5 ** (dt / half_life)
        factor = 0.5 ** (dt / self._decay_t)
        self._state.valence *= factor
        self._state.arousal *= factor
        self._state.stress  *= factor
        self._state.updated = time.time()

    @staticmethod
    def _lexicon_score(text: str) -> Tuple[float, float, float]:
        low = text.lower()
        words = re.findall(r"[\w']+", low)
        if not words:
            return 0.0, 0.0, 0.0
        pos = neg = strs = 0.0
        for w in words:
            if w in _LEX_POS: pos += _LEX_POS[w]
            elif w in _LEX_NEG: neg += -_LEX_NEG[w]   # робимо позитивним для суми
            if w in _LEX_STRESS: strs += _LEX_STRESS[w]
        # Перевіряємо біграми типу "не виходить", "не можу", "не працює".
        for i in range(len(words) - 1):
            bi = f"{words[i]} {words[i+1]}"
            if bi in _LEX_NEG:
                neg += -_LEX_NEG[bi]
            if bi in _LEX_STRESS:
                strs += _LEX_STRESS[bi]

        n = max(len(words), 5)
        valence = _clip((pos - neg) / (n ** 0.5), -1, 1)
        # Arousal: КАПСИ, !!, багато знаків питання, лайка → вище збудження.
        excl = text.count("!")
        caps = sum(1 for ch in text if ch.isupper())
        caps_ratio = caps / max(len(text), 1)
        arousal_raw = 0.15 * excl + 0.6 * caps_ratio + 0.4 * min(strs, 2.0)
        arousal = _clip(arousal_raw, 0, 1)
        stress = _clip(strs / 2.0 + 0.4 * max(0.0, neg / n), 0, 1)
        return valence, arousal, stress

    @staticmethod
    def _heuristic_audio(f: Dict[str, float]) -> Tuple[float, float, float]:
        """Дуже груба евристика. Замінюй на реальну модель через set_audio_scorer.

        Орієнтовні правила:
          • високий jitter + низький pitch_var + низька energy → втома (val<0, arousal низький)
          • високий pitch_var + швидке мовлення + висока energy → збудження (arousal↑)
          • дуже швидке мовлення + jitter → стрес
        """
        def g(k: str, default: float = 0.5) -> float:
            try: return _clip(float(f.get(k, default)), 0.0, 1.0)
            except Exception: return default

        pitch_var   = g("pitch_var", 0.4)
        speech_rate = g("speech_rate", 0.5)
        energy      = g("energy", 0.5)
        jitter      = g("jitter", 0.2)

        # Arousal зростає з pitch_var, speech_rate, energy.
        arousal = _clip(0.4 * pitch_var + 0.3 * speech_rate + 0.3 * energy, 0, 1)
        # Stress: швидке мовлення + jitter — головні маркери.
        stress  = _clip(0.5 * jitter + 0.4 * max(0.0, speech_rate - 0.6) * 2.5, 0, 1)
        # Valence — найслабший сигнал з аудіо без моделі: енергійний + не нервовий = +;
        # тиха + jitter = втома (-).
        valence = _clip((energy - 0.5) - 0.6 * jitter, -1, 1)
        return valence, arousal, stress


# ---------------------------------------------------------------------------
# Behavior hints — підказки моделі по labels.
# ---------------------------------------------------------------------------
_BEHAVIOR_HINTS = {
    "напружений":   "Відповідай коротко, по суті, без жартів і метафор.",
    "роздратований": "Не виправдовуйся, не повторюй питання, переходь до дії.",
    "втомлений":    "Будь м'яким і лаконічним; не пропонуй довгих планів.",
    "збуджений":    "Можеш бути живим і трохи грайливим, але тримай фокус.",
    "позитивний":   "Можна додати легкої теплоти, але без надмірності.",
    "спокійний":    "Звичайний тон; можна давати розгорнуті пояснення.",
    "нейтральний":  "",
}


def _clip(x: float, lo: float, hi: float) -> float:
    if x < lo: return lo
    if x > hi: return hi
    return x


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
def get_affect() -> Affect:
    # Зберігання делеговано у core.container (єдине джерело правди).
    from core.container import get_container
    return get_container().get_or_create("affect", Affect)
