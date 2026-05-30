"""Sentiment scorer for core.affect — реальна модель замість лексикону.

Завантажує мультимовний sentiment-класифікатор (за замовчуванням
`cardiffnlp/twitter-xlm-roberta-base-sentiment`) і реєструє його як
text_scorer для core.affect.

Особливості:
  • Лінива загрузка у фоновому потоці (не блокує старт).
  • Поки модель не готова — використовується вбудований лексикон.
  • CPU за замовчуванням; GPU якщо torch.cuda доступне і AFFECT_NLP_DEVICE != "cpu".
  • Кеш результатів на N останніх рядків — економимо токени на повторах.
  • Повний opt-out через `AFFECT_NLP=0` (тоді модуль навіть не імпортується).

Використання (виклик у main.py під час старту):

    from core.affect_nlp import enable_nlp_scorer
    enable_nlp_scorer()    # асинхронно; повертає одразу

Контракт скорера: (valence: float, arousal: float, stress: float).
  • valence  — pos_prob - neg_prob  ∈ [-1..+1]
  • arousal  — 1 - neu_prob (наскільки взагалі емоційний текст) ∈ [0..1]
  • stress   — neg_prob, посилений за впевненістю моделі            ∈ [0..1]
"""
from __future__ import annotations

import os
import logging
import threading
from collections import OrderedDict
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv(
    "AFFECT_NLP_MODEL",
    "cardiffnlp/twitter-xlm-roberta-base-sentiment",
)
# Кеш: рядок (нормалізований) → (valence, arousal, stress).
_CACHE_MAX = 256


class _SentimentScorer:
    """Завантажує модель один раз, безпечний у потоках. Без скоупу залежностей
    у constructor — імпорт transformers відкладений до фактичного _load."""

    def __init__(self, model_id: str = DEFAULT_MODEL):
        self._model_id = model_id
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._failed = False
        self._tokenizer = None
        self._model = None
        self._device = "cpu"
        self._cache: "OrderedDict[str, Tuple[float, float, float]]" = OrderedDict()
        self._labels: list[str] = []  # порядок з config.id2label

    # ------------------------------------------------------------------
    def start_async(self) -> None:
        """Запустити завантаження у фоновому потоці. Безпечно викликати
        кілька разів — другий виклик не зробить нічого."""
        if self._ready.is_set() or self._failed:
            return
        with self._lock:
            if getattr(self, "_loader_thread", None) is not None:
                return
            self._loader_thread = threading.Thread(
                target=self._load, name="AffectNLPLoader", daemon=True,
            )
            self._loader_thread.start()

    def is_ready(self) -> bool:
        return self._ready.is_set()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            import torch  # type: ignore
            from transformers import (  # type: ignore
                AutoTokenizer, AutoModelForSequenceClassification,
            )
        except Exception as e:
            logger.warning(f"[affect_nlp] transformers/torch недоступні: {e}; "
                           f"залишаюсь на лексиконі.")
            self._failed = True
            return

        device_pref = os.getenv("AFFECT_NLP_DEVICE", "auto").lower()
        if device_pref != "cpu" and torch.cuda.is_available():
            self._device = "cuda"
        else:
            self._device = "cpu"

        try:
            logger.info(f"[affect_nlp] завантажую {self._model_id} ({self._device})…")
            tok = AutoTokenizer.from_pretrained(self._model_id)
            mdl = AutoModelForSequenceClassification.from_pretrained(self._model_id)
            mdl.eval()
            mdl.to(self._device)

            # cardiffnlp моделі мають config.id2label = {0:'negative',1:'neutral',2:'positive'}.
            id2label = getattr(mdl.config, "id2label", None) or {}
            labels = [str(id2label[i]).lower() for i in sorted(id2label)]
            if not labels:
                labels = ["negative", "neutral", "positive"]
            with self._lock:
                self._tokenizer = tok
                self._model = mdl
                self._labels = labels
            self._ready.set()
            logger.info(f"[affect_nlp] модель готова: labels={labels}")
        except Exception as e:
            logger.warning(f"[affect_nlp] завантаження впало: {e}; залишаюсь на лексиконі.")
            self._failed = True

    # ------------------------------------------------------------------
    def score(self, text: str) -> Tuple[float, float, float]:
        """Контракт core.affect.TextScorer. Якщо модель не готова → лексикон."""
        if not self._ready.is_set():
            return _lexicon_fallback(text)
        key = (text or "").strip()[:300].lower()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        try:
            v, a, s = self._infer(text)
        except Exception as e:
            logger.debug(f"[affect_nlp] inference failed: {e}; fallback to lexicon.")
            return _lexicon_fallback(text)
        with self._lock:
            self._cache[key] = (v, a, s)
            if len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
        return v, a, s

    def _infer(self, text: str) -> Tuple[float, float, float]:
        import torch  # type: ignore
        tok = self._tokenizer
        mdl = self._model
        if tok is None or mdl is None:
            return _lexicon_fallback(text)

        enc = tok(text, return_tensors="pt", truncation=True, max_length=128)
        enc = {k: v.to(self._device) for k, v in enc.items()}
        with torch.no_grad():
            logits = mdl(**enc).logits[0]
            probs = torch.softmax(logits, dim=-1).detach().cpu().tolist()

        # Мапимо мітки на pos/neg/neu — для будь-якого порядку id2label.
        p_neg = p_neu = p_pos = 0.0
        for label, p in zip(self._labels, probs):
            l = label.lower()
            if l.startswith("neg"):    p_neg = float(p)
            elif l.startswith("pos"):  p_pos = float(p)
            elif l.startswith("neu"):  p_neu = float(p)
            else:                       p_neu += float(p)  # невідомі → нейтральне відро

        valence = max(-1.0, min(1.0, p_pos - p_neg))
        arousal = max(0.0, min(1.0, 1.0 - p_neu))
        # Stress: значущий лише при високій впевненості негативу.
        stress = max(0.0, min(1.0, p_neg ** 1.4))
        return valence, arousal, stress


# ---------------------------------------------------------------------------
def _lexicon_fallback(text: str) -> Tuple[float, float, float]:
    """Поки модель не готова — повертаємо результат вбудованого лексикону."""
    try:
        from core.affect import Affect
        return Affect._lexicon_score(text)
    except Exception:
        return 0.0, 0.0, 0.0


# ---------------------------------------------------------------------------
_INSTANCE: Optional[_SentimentScorer] = None
_INSTANCE_LOCK = threading.Lock()


def enable_nlp_scorer(model_id: Optional[str] = None) -> bool:
    """Активувати NLP-скорер у core.affect. Повертає True якщо запущено
    (фактична готовність — пізніше; до того часу працює лексикон).

    Налаштування через env:
      AFFECT_NLP=0           — повний opt-out, нічого не вантажиться.
      AFFECT_NLP_MODEL=...   — інша модель.
      AFFECT_NLP_DEVICE=cpu  — примусово CPU навіть при наявній GPU.
    """
    if os.getenv("AFFECT_NLP", "1") == "0":
        logger.info("[affect_nlp] вимкнено через AFFECT_NLP=0.")
        return False

    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = _SentimentScorer(model_id or DEFAULT_MODEL)

    try:
        from core.affect import get_affect
        get_affect().set_text_scorer(_INSTANCE.score)
    except Exception as e:
        logger.warning(f"[affect_nlp] не вдалося прив'язати scorer: {e}")
        return False

    _INSTANCE.start_async()
    return True


def get_scorer() -> Optional[_SentimentScorer]:
    return _INSTANCE
