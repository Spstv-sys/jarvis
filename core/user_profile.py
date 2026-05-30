"""User Profile — стабільне самопредставлення «мого господаря».

Зберігається у `JarvisDatabase.settings` під ключем ``user_profile_v1`` як
JSON. Це не заміна RAG/episodic — це **малий, незмінний контекст**, який
завжди підмішується у системний промпт LLM, щоб Нео не забував:
  • як звати користувача,
  • як до нього звертатися (ти/ви),
  • якою мовою відповідати,
  • часовий пояс,
  • годинник тиші (не турбувати з проактивними пропозиціями),
  • короткий список інтересів (для small-talk і анти­ципатора),
  • будь-які додаткові ноти у вільній формі.

API навмисно простий: get/update/as_prompt_block. Усі поля опційні,
відсутні значення просто пропускаються.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROFILE_KEY = "user_profile_v1"

DEFAULT_PROFILE: Dict[str, Any] = {
    "name": None,            # str | None — як звертатися до користувача
    "address_form": "ти",    # "ти" | "ви"
    "language": "uk",        # ISO 639-1
    "timezone": None,        # IANA tz name, напр. "Europe/Kyiv"
    "quiet_hours": None,     # {"from": "23:00", "to": "07:00"} | None
    "interests": [],         # list[str]
    "notes": None,           # str | None — вільна форма
}

ALLOWED_KEYS = set(DEFAULT_PROFILE.keys())


class UserProfile:
    """Тонкий wrapper над settings-таблицею з кешем у RAM."""

    def __init__(self, db) -> None:
        self._db = db
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] = dict(DEFAULT_PROFILE)
        self._loaded = False
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = self._db.get_setting(PROFILE_KEY)
            if not raw:
                self._loaded = True
                return
            data = json.loads(raw)
            if isinstance(data, dict):
                with self._lock:
                    for k, v in data.items():
                        if k in ALLOWED_KEYS:
                            self._cache[k] = v
            self._loaded = True
        except Exception as e:
            logger.warning(f"[UserProfile] завантаження не вдалося: {e}")
            self._loaded = True

    def _save(self) -> None:
        try:
            self._db.set_setting(PROFILE_KEY, json.dumps(self._cache, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[UserProfile] збереження не вдалося: {e}")

    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._cache.get(key, default)

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._cache)

    def update(self, **fields: Any) -> Dict[str, Any]:
        """Оновити профіль. Нерозпізнані ключі ігноруються."""
        with self._lock:
            for k, v in fields.items():
                if k not in ALLOWED_KEYS:
                    logger.debug(f"[UserProfile] ігнорую невідомий ключ: {k}")
                    continue
                self._cache[k] = v
            self._save()
            return dict(self._cache)

    def add_interest(self, topic: str) -> None:
        topic = (topic or "").strip()
        if not topic:
            return
        with self._lock:
            interests: List[str] = list(self._cache.get("interests") or [])
            low = topic.lower()
            if any(i.lower() == low for i in interests):
                return
            interests.append(topic)
            # Тримаємо короткий список, щоб не роздувати промпт.
            if len(interests) > 12:
                interests = interests[-12:]
            self._cache["interests"] = interests
            self._save()

    def remove_interest(self, topic: str) -> bool:
        topic = (topic or "").strip().lower()
        if not topic:
            return False
        with self._lock:
            interests = list(self._cache.get("interests") or [])
            new = [i for i in interests if i.lower() != topic]
            if len(new) == len(interests):
                return False
            self._cache["interests"] = new
            self._save()
            return True

    # ------------------------------------------------------------------
    def as_prompt_block(self) -> str:
        """Короткий блок для системного промпту. Повертає '' якщо профіль порожній."""
        with self._lock:
            p = dict(self._cache)
        parts: List[str] = []
        if p.get("name"):
            form = p.get("address_form") or "ти"
            parts.append(f"Користувача звати {p['name']}; звертайся на «{form}».")
        if p.get("language") and p["language"] != "uk":
            parts.append(f"Відповідай мовою: {p['language']}.")
        if p.get("timezone"):
            parts.append(f"Часовий пояс: {p['timezone']}.")
        qh = p.get("quiet_hours") or {}
        if isinstance(qh, dict) and qh.get("from") and qh.get("to"):
            parts.append(f"Години тиші: {qh['from']}–{qh['to']} (не турбувати проактивно).")
        interests = p.get("interests") or []
        if interests:
            parts.append("Інтереси користувача: " + ", ".join(interests) + ".")
        if p.get("notes"):
            parts.append(f"Нотатки про користувача: {p['notes']}.")
        return " ".join(parts)


# ----------------------------------------------------------------------
_GLOBAL: Optional[UserProfile] = None
_GLOBAL_LOCK = threading.Lock()


def get_user_profile(db) -> UserProfile:
    """Lazy-singleton. Перший виклик прив'язує до конкретної БД."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = UserProfile(db)
        return _GLOBAL
