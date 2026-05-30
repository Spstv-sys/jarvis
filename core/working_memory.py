"""Working Memory — короткострокова свідомість Джарвіса.

Зберігає контекст ПОТОЧНОГО діалогу, який не існує у RAG/SQLite:
  • активна тема (про що зараз говоримо)
  • активні сутності (особи, місця, об'єкти, що згадувалися)
  • останній скіл/дія (для "повтори", "ще раз", "продовжи")
  • короткий буфер реплік як структура (не лише рядки)
  • незавершені цілі (Goal — користувач щось почав, чекає продовження)

Завдання — підмішувати `context_block()` у промпт LLM та
розв'язувати кореференцію ("він", "вона", "той", "цей") до останньої
згаданої сутності того ж роду.

Опис інтегрується з config.WORKING_MEMORY_* налаштуваннями (опційно).
Все потокобезпечне через RLock.

Серіалізація: `dump()` повертає JSON-сумісний dict зі станом, `load()`
відновлює його. Використовується JarvisUltimate для збереження топ-сутностей
та активної теми між рестартами (через settings-таблицю БД).
"""
from __future__ import annotations

import re
import json
import time
import threading
import logging
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Дані
# ---------------------------------------------------------------------------
@dataclass
class Turn:
    """Одна репліка діалогу зі структурою."""
    role: str                 # "user" | "ai" | "system"
    text: str
    ts: float = field(default_factory=time.time)
    intent: Optional[str] = None      # "chitchat" / "skill_call" / "kg_query" / ...
    skill: Optional[str] = None
    entities: List[str] = field(default_factory=list)


@dataclass
class Entity:
    """Сутність, що активна в розмові."""
    name: str
    kind: str = "misc"          # person/location/work/event/organization/misc
    last_seen: float = field(default_factory=time.time)
    mentions: int = 1
    source: Optional[str] = None  # книга/сайт/скіл, звідки взято


@dataclass
class Goal:
    """Незавершена ціль користувача."""
    description: str
    created: float = field(default_factory=time.time)
    deadline: Optional[float] = None
    done: bool = False


# ---------------------------------------------------------------------------
# Кореференція (наївна, але робоча для української)
# ---------------------------------------------------------------------------
# чоловічий рід / жіночий / середній / множина — для вибору сутності.
_PRONOUNS = {
    "m": {"він", "його", "йому", "ним", "цей", "той", "він-же", "той самий"},
    "f": {"вона", "її", "їй", "нею", "ця", "та", "та сама"},
    "n": {"воно", "це", "те", "цього", "того"},
    "p": {"вони", "їх", "їм", "ними", "ці", "ті"},
}

# Для дуже грубої здогадки роду по закінченню імені/назви.
def _guess_gender(name: str) -> str:
    n = name.lower().strip()
    if not n:
        return "n"
    last = n[-1]
    if last in "аяю":      # Марія, Наталя
        return "f"
    if last in "оеєиі":     # Дніпро, Поле
        return "n"
    return "m"              # Шевченко, Київ, Кайдаш


# ---------------------------------------------------------------------------
# Витягання сутностей з тексту (евристика без spaCy: слова з великої літери,
# не на початку речення, не зі стоп-словника).
# ---------------------------------------------------------------------------
_STOPWORDS_CAP = {
    "Джарвіс", "Нео", "Neo", "Jarvis", "Я", "Ти", "Ми", "Ви", "Вони", "Він", "Вона", "Воно",
    "Так", "Ні", "Будь", "Бо", "Але", "Якщо", "Коли", "Де", "Що", "Хто",
    "Україна", "Україні", "Україну",  # надто часті — ігноруємо як активну
    # Поширені українські імперативи (частий початок голосових команд):
    "Розкажи", "Скажи", "Покажи", "Знайди", "Відкрий", "Закрий", "Запусти",
    "Дай", "Зроби", "Напиши", "Прочитай", "Видали", "Додай", "Згадай",
    "Перевір", "Порахуй", "Поясни", "Опиши", "Згенеруй", "Створи", "Видалити",
    "Слухай", "Привіт", "Будьласка", "Будь-ласка",
    # Місяці/дні часто капіталізовані без потреби:
    "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
}

_RE_CAP = re.compile(r"\b([А-ЯІЇЄҐA-Z][а-яіїєґa-zʼ'\-]{2,})\b")
_RE_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)")


def _extract_entities_heuristic(text: str, limit: int = 5) -> List[str]:
    """Швидка евристика: великі літери всередині речення → ймовірна сутність."""
    if not text:
        return []
    found: List[str] = []
    seen: set = set()
    # Маркуємо позиції початку речень — щоб слово після крапки не вважати ім'ям.
    sentence_starts = {m.end() for m in _RE_SENTENCE_START.finditer(text)}
    sentence_starts.add(0)

    for m in _RE_CAP.finditer(text):
        word = m.group(1)
        if word in _STOPWORDS_CAP:
            continue
        if word.lower() in seen:
            continue
        # Слово на самому початку речення → можливо просто капіталізоване.
        # Залишаємо його лише якщо складається з ≥4 літер.
        if m.start() in sentence_starts and len(word) < 4:
            continue
        seen.add(word.lower())
        found.append(word)
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------
class WorkingMemory:
    """Свідома короткочасна пам'ять Джарвіса.

    Зберігається в RAM, не персистується — це саме "тут і зараз".
    Якщо буде треба — серіалізація через `dump()` / `load()`.
    """

    def __init__(
        self,
        buffer_size: int = 12,
        topic_ttl_sec: int = 600,
        entity_ttl_sec: int = 1200,
        max_entities: int = 12,
    ):
        self._lock = threading.RLock()
        self.turns: Deque[Turn] = deque(maxlen=buffer_size)
        self.entities: Dict[str, Entity] = {}      # name_norm → Entity
        self.active_topic: Optional[str] = None
        self.topic_set_at: float = 0.0
        self.last_skill: Optional[str] = None
        self.last_action_result: Optional[str] = None
        self.pending_goals: List[Goal] = []

        self.topic_ttl = topic_ttl_sec
        self.entity_ttl = entity_ttl_sec
        self.max_entities = max_entities

    # ----------------------------------------------- запис
    def add_user_turn(self, text: str, intent: Optional[str] = None) -> None:
        with self._lock:
            ents = _extract_entities_heuristic(text)
            self.turns.append(Turn(role="user", text=text, intent=intent, entities=ents))
            for e in ents:
                self._touch_entity(e)

    def add_ai_turn(self, text: str, skill: Optional[str] = None) -> None:
        with self._lock:
            ents = _extract_entities_heuristic(text)
            self.turns.append(Turn(role="ai", text=text, skill=skill, entities=ents))
            if skill:
                self.last_skill = skill
                self.last_action_result = (text or "")[:400]
            for e in ents:
                self._touch_entity(e)

    def set_topic(self, topic: str) -> None:
        with self._lock:
            self.active_topic = topic
            self.topic_set_at = time.time()

    def register_entity(self, name: str, kind: str = "misc", source: Optional[str] = None) -> None:
        """Викликається скілами (KG, RAG), коли вони ідентифікували конкретну сутність."""
        with self._lock:
            self._touch_entity(name, kind=kind, source=source)

    def add_goal(self, description: str, deadline: Optional[float] = None) -> None:
        with self._lock:
            self.pending_goals.append(Goal(description=description, deadline=deadline))

    def complete_goal(self, description_substring: str) -> bool:
        with self._lock:
            for g in self.pending_goals:
                if not g.done and description_substring.lower() in g.description.lower():
                    g.done = True
                    return True
            return False

    def clear(self, hard: bool = False) -> None:
        """Скинути контекст. ``hard=True`` — повне очищення, інакше лише сутності/тема."""
        with self._lock:
            self.entities.clear()
            self.active_topic = None
            self.topic_set_at = 0.0
            self.last_skill = None
            self.last_action_result = None
            self.pending_goals = [g for g in self.pending_goals if not g.done]
            if hard:
                self.turns.clear()
                self.pending_goals.clear()

    # ----------------------------------------------- читання
    def context_block(self, max_chars: int = 800) -> str:
        """Стислий текстовий блок для підмішування у промпт LLM."""
        with self._lock:
            self._evict_stale()
            parts: List[str] = []

            if self.active_topic:
                parts.append(f"Поточна тема: {self.active_topic}.")

            top_entities = sorted(
                self.entities.values(),
                key=lambda e: (e.mentions, e.last_seen),
                reverse=True,
            )[:6]
            if top_entities:
                ents_str = ", ".join(
                    f"{e.name} ({e.kind})" if e.kind != "misc" else e.name
                    for e in top_entities
                )
                parts.append(f"Активні сутності: {ents_str}.")

            if self.last_skill:
                parts.append(f"Остання дія: {self.last_skill}.")

            unfinished = [g.description for g in self.pending_goals if not g.done][:3]
            if unfinished:
                parts.append("Незавершені цілі: " + "; ".join(unfinished) + ".")

            block = " ".join(parts)
            return block[:max_chars]

    def recent_dialog(self, n: int = 4) -> str:
        with self._lock:
            tail = list(self.turns)[-n:]
            return "\n".join(f"{t.role.upper()}: {t.text}" for t in tail)

    def last_entity_of_gender(self, gender: str) -> Optional[Entity]:
        """Повертає найсвіжішу сутність відповідного роду (для кореференції)."""
        with self._lock:
            cands = [
                e for e in self.entities.values()
                if _guess_gender(e.name) == gender
            ]
            if not cands:
                return None
            cands.sort(key=lambda e: e.last_seen, reverse=True)
            return cands[0]

    def resolve_coreferences(self, text: str) -> str:
        """Підмінює займенники на реальні імена з активних сутностей.

        Дуже наївно — лише першу появу і лише якщо є кандидат.
        Не змінює оригінал, якщо нема впевненості.
        """
        if not text:
            return text
        lowered = text.lower()
        for gender, prons in _PRONOUNS.items():
            for pron in prons:
                # Шукаємо як окреме слово.
                pattern = re.compile(rf"\b{re.escape(pron)}\b", re.IGNORECASE)
                if not pattern.search(lowered):
                    continue
                ent = self.last_entity_of_gender(gender)
                if not ent:
                    continue
                # Підставляємо в дужках, не ламаючи оригінал.
                replaced = pattern.sub(f"{pron} ({ent.name})", text, count=1)
                if replaced != text:
                    text = replaced
                    lowered = text.lower()
                    break  # одну заміну за рід вистачить
        return text

    def stats(self) -> dict:
        with self._lock:
            return {
                "turns": len(self.turns),
                "entities": len(self.entities),
                "topic": self.active_topic,
                "last_skill": self.last_skill,
                "pending_goals": sum(1 for g in self.pending_goals if not g.done),
            }

    # ----------------------------------------------- персистенція
    # Стан WM зазвичай ефемерний, але мінімальний "залишок" (топ-сутності,
    # активна тема, незавершені цілі) корисно перенести через рестарт —
    # щоб Нео не починав щоразу з повного нуля.

    SNAPSHOT_VERSION = 1
    SNAPSHOT_MAX_TURNS = 6
    SNAPSHOT_MAX_ENTITIES = 10
    SNAPSHOT_MAX_AGE_SEC = 24 * 3600  # >24h — вважаємо несвіжим

    def dump(self) -> dict:
        """Повертає JSON-сумісний знімок стану для збереження."""
        with self._lock:
            self._evict_stale()
            top_entities = sorted(
                self.entities.values(),
                key=lambda e: (e.mentions, e.last_seen),
                reverse=True,
            )[: self.SNAPSHOT_MAX_ENTITIES]
            return {
                "version": self.SNAPSHOT_VERSION,
                "saved_at": time.time(),
                "active_topic": self.active_topic,
                "topic_set_at": self.topic_set_at,
                "last_skill": self.last_skill,
                "last_action_result": self.last_action_result,
                "turns": [asdict(t) for t in list(self.turns)[-self.SNAPSHOT_MAX_TURNS:]],
                "entities": [asdict(e) for e in top_entities],
                "pending_goals": [
                    asdict(g) for g in self.pending_goals if not g.done
                ][:5],
            }

    def load(self, snapshot: dict) -> bool:
        """Відновлює стан зі знімка. Повертає True якщо завантажено."""
        if not isinstance(snapshot, dict):
            return False
        if snapshot.get("version") != self.SNAPSHOT_VERSION:
            logger.info("[WM] snapshot version mismatch — пропуск")
            return False
        saved_at = float(snapshot.get("saved_at") or 0)
        if saved_at and (time.time() - saved_at) > self.SNAPSHOT_MAX_AGE_SEC:
            logger.info("[WM] snapshot застарілий (>24h) — пропуск")
            return False
        try:
            with self._lock:
                self.active_topic = snapshot.get("active_topic")
                self.topic_set_at = float(snapshot.get("topic_set_at") or 0)
                self.last_skill = snapshot.get("last_skill")
                self.last_action_result = snapshot.get("last_action_result")
                # Turns: відновлюємо лише поля, які є в Turn dataclass.
                self.turns.clear()
                for td in snapshot.get("turns") or []:
                    try:
                        self.turns.append(Turn(
                            role=td.get("role", "user"),
                            text=td.get("text", ""),
                            ts=float(td.get("ts") or time.time()),
                            intent=td.get("intent"),
                            skill=td.get("skill"),
                            entities=list(td.get("entities") or []),
                        ))
                    except Exception:
                        continue
                self.entities.clear()
                for ed in snapshot.get("entities") or []:
                    try:
                        name = ed.get("name") or ""
                        if not name:
                            continue
                        self.entities[name.lower().strip()] = Entity(
                            name=name,
                            kind=ed.get("kind", "misc"),
                            last_seen=float(ed.get("last_seen") or time.time()),
                            mentions=int(ed.get("mentions") or 1),
                            source=ed.get("source"),
                        )
                    except Exception:
                        continue
                self.pending_goals = []
                for gd in snapshot.get("pending_goals") or []:
                    try:
                        self.pending_goals.append(Goal(
                            description=gd.get("description", ""),
                            created=float(gd.get("created") or time.time()),
                            deadline=gd.get("deadline"),
                            done=bool(gd.get("done", False)),
                        ))
                    except Exception:
                        continue
            logger.info(
                "[WM] snapshot завантажено: %d turns, %d entities, topic=%r",
                len(self.turns), len(self.entities), self.active_topic,
            )
            return True
        except Exception as e:
            logger.warning(f"[WM] не вдалося застосувати snapshot: {e}")
            return False

    def to_json(self) -> str:
        return json.dumps(self.dump(), ensure_ascii=False)

    def from_json(self, raw: str) -> bool:
        if not raw:
            return False
        try:
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"[WM] невалідний JSON snapshot: {e}")
            return False
        return self.load(data)

    # ----------------------------------------------- внутрішнє
    def _touch_entity(self, name: str, kind: str = "misc", source: Optional[str] = None) -> None:
        if not name:
            return
        key = name.lower().strip()
        if key in self.entities:
            e = self.entities[key]
            e.mentions += 1
            e.last_seen = time.time()
            if kind != "misc" and e.kind == "misc":
                e.kind = kind
            if source and not e.source:
                e.source = source
        else:
            self.entities[key] = Entity(
                name=name, kind=kind, source=source
            )

        # Тримаємо обмеження.
        if len(self.entities) > self.max_entities:
            # Викидаємо найстарішу/найменш згадувану.
            victim = min(
                self.entities.items(),
                key=lambda kv: (kv[1].mentions, kv[1].last_seen),
            )
            self.entities.pop(victim[0], None)

    def _evict_stale(self) -> None:
        now = time.time()
        if self.active_topic and (now - self.topic_set_at) > self.topic_ttl:
            self.active_topic = None
            self.topic_set_at = 0.0
        # Видаляємо застарілі сутності.
        stale = [k for k, e in self.entities.items() if (now - e.last_seen) > self.entity_ttl]
        for k in stale:
            self.entities.pop(k, None)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_GLOBAL: Optional[WorkingMemory] = None
_GLOBAL_LOCK = threading.Lock()


def get_working_memory() -> WorkingMemory:
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = WorkingMemory()
        return _GLOBAL
