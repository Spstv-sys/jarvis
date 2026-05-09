"""Intent Router — гібридний класифікатор намірів.

Архітектура (3 рівні, fail-fast):

  1. FAST PATH — substring / word-boundary / fuzzy match по config.TRIGGERS.
     Покриває ~80% типових команд миттєво (без LLM).

  2. CACHE — нормалізований SHA-1 хеш команди → останній intent.
     Зберігається у SQLite. Користувач сказав те саме вдруге — миттєвий результат.

  3. LLM FALLBACK — якщо нічого не зматчилось, питаємо локальну/активну LLM
     повернути JSON {intent, confidence, reasoning, args?}. Кешується.

Інтегрується ПОВЕРХ існуючої логіки execute_command — дзеркально, не ламаючи її.
Якщо LLM недоступний → повертає Intent("chat", ...) як безпечний фолбек.
"""
from __future__ import annotations

import os
import re
import json
import time
import hashlib
import sqlite3
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Дані
# ---------------------------------------------------------------------------
@dataclass
class Intent:
    name: str                                  # ключ маршруту або "chat" / "clarify"
    confidence: float = 1.0                    # 0..1
    source: str = "fast"                       # "fast" | "cache" | "llm" | "fallback"
    reasoning: Optional[str] = None
    args: Dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# IntentRouter
# ---------------------------------------------------------------------------
class IntentRouter:
    """Класифікує сиру користувацьку команду в один із відомих маршрутів."""

    # SQL-схема кешу.
    _SQL_INIT = """
    CREATE TABLE IF NOT EXISTS intent_cache (
        cmd_hash    TEXT PRIMARY KEY,
        cmd_sample  TEXT NOT NULL,
        intent      TEXT NOT NULL,
        confidence  REAL NOT NULL,
        source      TEXT NOT NULL,
        ts          REAL NOT NULL,
        hits        INTEGER NOT NULL DEFAULT 1
    );
    """

    def __init__(
        self,
        triggers: Dict[str, List[str]],
        dynamic_skills: Optional[List[dict]] = None,
        llm_call: Optional[Callable[[str], Optional[str]]] = None,
        cache_db_path: Optional[str] = None,
        fuzzy_match: Optional[Callable[[str, str, int], bool]] = None,
        fuzzy_threshold: int = 80,
        cache_ttl_sec: int = 30 * 24 * 3600,
        llm_min_chars: int = 4,
        llm_max_chars: int = 400,
    ):
        self._triggers = triggers
        self._dynamic_skills = dynamic_skills or []
        self._llm_call = llm_call
        self._fuzzy = fuzzy_match
        self._fuzzy_threshold = fuzzy_threshold
        self._cache_ttl = cache_ttl_sec
        self._llm_min_chars = llm_min_chars
        self._llm_max_chars = llm_max_chars

        self._lock = threading.RLock()
        self._db_path = cache_db_path
        self._conn: Optional[sqlite3.Connection] = None
        if cache_db_path:
            self._init_db(cache_db_path)

        # Перелік відомих маршрутів = ключі triggers + "chat" + "clarify"
        self._known_routes = sorted(set(triggers.keys()) | {"chat", "clarify"})

    # ----------------------------------------------------------------- DB
    def _init_db(self, path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.execute(self._SQL_INIT)
            self._conn.commit()
        except Exception as e:
            logger.warning(f"[IntentRouter] не вдалося ініціалізувати кеш {path}: {e}")
            self._conn = None

    @staticmethod
    def _hash(cmd: str) -> str:
        norm = re.sub(r"\s+", " ", cmd.strip().lower())
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()

    def _cache_get(self, cmd: str) -> Optional[Intent]:
        if not self._conn:
            return None
        try:
            with self._lock:
                cur = self._conn.execute(
                    "SELECT intent, confidence, source, ts FROM intent_cache WHERE cmd_hash=?",
                    (self._hash(cmd),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                intent, conf, source, ts = row
                if (time.time() - ts) > self._cache_ttl:
                    return None
                # Інкремент hits.
                self._conn.execute(
                    "UPDATE intent_cache SET hits = hits + 1 WHERE cmd_hash=?",
                    (self._hash(cmd),),
                )
                self._conn.commit()
                return Intent(name=intent, confidence=float(conf), source="cache",
                              reasoning=f"cached from {source}")
        except Exception as e:
            logger.warning(f"[IntentRouter] cache_get fail: {e}")
            return None

    def _cache_put(self, cmd: str, intent: Intent) -> None:
        if not self._conn:
            return
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO intent_cache (cmd_hash, cmd_sample, intent, confidence, source, ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cmd_hash) DO UPDATE SET
                        intent=excluded.intent,
                        confidence=excluded.confidence,
                        source=excluded.source,
                        ts=excluded.ts,
                        hits=hits+1
                    """,
                    (self._hash(cmd), cmd[:120], intent.name,
                     intent.confidence, intent.source, time.time()),
                )
                self._conn.commit()
        except Exception as e:
            logger.warning(f"[IntentRouter] cache_put fail: {e}")

    # --------------------------------------------------------------- core
    def classify(self, cmd: str) -> Intent:
        """Повертає найкращий Intent для команди."""
        from core.thought_log import think, STAGE_INTENT
        if not cmd or not cmd.strip():
            return Intent(name="chat", confidence=0.0, source="fallback",
                          reasoning="empty input")

        intent = self._classify_inner(cmd)
        try:
            think(STAGE_INTENT,
                  f"{intent.source}: {intent.name} (conf={intent.confidence:.2f})",
                  cmd=cmd[:120], reasoning=intent.reasoning[:120])
        except Exception:
            pass
        return intent

    def _classify_inner(self, cmd: str) -> Intent:
        # 1) Динамічні скіли — найвищий пріоритет.
        skill_match = self._match_dynamic_skill(cmd)
        if skill_match:
            intent = Intent(
                name=f"skill:{skill_match}",
                confidence=1.0,
                source="fast",
                reasoning=f"dynamic skill '{skill_match}' triggered",
            )
            return intent  # не кешуємо: набір скілів змінюється

        # 2) Built-in routes по тригерах.
        route_match = self._match_route(cmd)
        if route_match:
            return Intent(
                name=route_match[0],
                confidence=route_match[1],
                source="fast",
                reasoning=f"trigger '{route_match[2]}'",
            )

        # 3) Кеш.
        cached = self._cache_get(cmd)
        if cached:
            return cached

        # 4) LLM-fallback.
        if self._llm_call and self._llm_min_chars <= len(cmd) <= self._llm_max_chars:
            llm_intent = self._llm_classify(cmd)
            if llm_intent:
                self._cache_put(cmd, llm_intent)
                return llm_intent

        # 5) Тиха фолбек-відповідь — діалог з LLM.
        return Intent(name="chat", confidence=0.3, source="fallback",
                      reasoning="no trigger / cache / llm match")

    # ---------------------------------------------------------- matching
    def _match_dynamic_skill(self, cmd: str) -> Optional[str]:
        cmd_l = cmd.lower()
        for skill in self._dynamic_skills:
            for t in skill.get("triggers", []):
                if t.lower() in cmd_l:
                    return skill.get("name") or skill.get("id") or "?"
                if self._fuzzy and len(t.split()) >= 2 and self._fuzzy(cmd, t, self._fuzzy_threshold):
                    return skill.get("name") or skill.get("id") or "?"
        return None

    def _match_route(self, cmd: str) -> Optional[Tuple[str, float, str]]:
        """Повертає (route_key, confidence, matched_trigger) або None."""
        cmd_l = cmd.lower()
        # Сортуємо тригери за довжиною (від найдовшого) щоб уникнути
        # передчасного матчу коротких підрядків.
        for key, triggers in self._triggers.items():
            for trigger in sorted(triggers, key=len, reverse=True):
                t_l = trigger.lower()
                # Точний словесний match
                if re.search(rf"\b{re.escape(t_l)}\b", cmd_l):
                    return (key, 1.0, trigger)
                # Substring (legacy)
                if t_l in cmd_l:
                    return (key, 0.9, trigger)
                # Fuzzy для багатослівних тригерів
                if (self._fuzzy
                        and len(trigger.split()) >= 2
                        and self._fuzzy(cmd, trigger, self._fuzzy_threshold)):
                    return (key, 0.75, trigger)
        return None

    # --------------------------------------------------------------- LLM
    _LLM_PROMPT_TPL = (
        "Ти — класифікатор намірів україномовного голосового асистента.\n"
        "Команда користувача нижче. Обери ОДИН найкращий маршрут зі списку.\n\n"
        "Доступні маршрути (key — короткий опис):\n{routes_desc}\n\n"
        "ОСОБЛИВІ маршрути:\n"
        "  • chat — звичайна розмова / питання знань, не потребує спецдії\n"
        "  • clarify — команда нечітка, треба перепитати користувача\n\n"
        "ВІДПОВІДЬ — тільки JSON у одному рядку, без жодного тексту довкола:\n"
        '  {{"intent": "<key>", "confidence": <0..1>, "reasoning": "<коротко>"}}\n\n'
        "Команда: {cmd!r}"
    )

    # Короткі описи відомих маршрутів — для промпту.
    _ROUTE_DESCRIPTIONS = {
        "update_knowledge": "індексація документів",
        "learn_skill":      "написати нову навичку (код)",
        "activate_skill":   "активувати очікувану навичку",
        "weather_exact":    "точна погода (синоптик)",
        "weather":          "погода / температура",
        "youtube_play":     "увімкнути музику/відео на YouTube",
        "youtube_search":   "пошук на YouTube",
        "news":             "останні новини",
        "finance":          "курс валют / фінанси",
        "switch_ai":        "змінити AI-провайдер",
        "personality":      "змінити стиль/режим відповіді",
        "check_ai":         "який AI зараз активний",
        "timer":            "таймер / нагадування",
        "hardware":         "звук, гучність, яскравість",
        "apps_os":          "відкрити/закрити програму чи папку",
        "vision":           "що на екрані / аналіз монітора",
        "code_assistant":   "робота з кодом",
        "calendar":         "календар / задачі / план",
        "dictation":        "режим диктування",
        "system_exit":      "вимкнути комп'ютер / завершити",
        "stop_voice":       "припинити говорити",
        "memorize":         "запам'ятати факт",
        "web_search":       "пошук в інтернеті / факти / 'хто такий'",
        "find_file":        "пошук файлу на диску",
    }

    def _llm_classify(self, cmd: str) -> Optional[Intent]:
        try:
            routes_desc = "\n".join(
                f"  • {k} — {self._ROUTE_DESCRIPTIONS.get(k, '?')}"
                for k in self._triggers.keys()
            )
            prompt = self._LLM_PROMPT_TPL.format(routes_desc=routes_desc, cmd=cmd)
            raw = self._llm_call(prompt)
            if not raw:
                return None
            parsed = self._parse_llm_json(raw)
            if not parsed:
                return None
            intent_name = parsed.get("intent", "").strip()
            if intent_name not in self._known_routes:
                logger.info(f"[IntentRouter] LLM повернув невідомий маршрут '{intent_name}', → chat")
                return Intent(name="chat", confidence=0.3, source="llm",
                              reasoning=f"LLM said {intent_name!r}, not in routes")
            return Intent(
                name=intent_name,
                confidence=float(parsed.get("confidence", 0.6) or 0.6),
                source="llm",
                reasoning=str(parsed.get("reasoning", ""))[:200],
            )
        except Exception as e:
            logger.warning(f"[IntentRouter] LLM-класифікація впала: {e}")
            return None

    @staticmethod
    def _parse_llm_json(raw: str) -> Optional[dict]:
        if not raw:
            return None
        # Пробуємо вирізати перший JSON-об'єкт.
        m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            # Спроба полагодити одинарні лапки.
            patched = m.group(0).replace("'", '"')
            try:
                return json.loads(patched)
            except Exception:
                return None

    # -------------------------------------------------------------- utils
    def stats(self) -> dict:
        info = {
            "known_routes": len(self._known_routes),
            "dynamic_skills": len(self._dynamic_skills),
            "llm_enabled": self._llm_call is not None,
            "cache": False,
        }
        if self._conn:
            try:
                with self._lock:
                    cur = self._conn.execute(
                        "SELECT COUNT(*), SUM(hits) FROM intent_cache"
                    )
                    n, hits = cur.fetchone()
                    info["cache"] = {"entries": n or 0, "total_hits": hits or 0}
            except Exception:
                pass
        return info

    def update_dynamic_skills(self, skills: List[dict]) -> None:
        """Викликається при перезавантаженні скілів на льоту."""
        with self._lock:
            self._dynamic_skills = skills
