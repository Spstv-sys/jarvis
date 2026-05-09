"""Knowledge Graph поверх RAG: сутності (персонажі, місця, події) + зв'язки.

Зберігання: SQLite (без зовнішніх залежностей). Витягування: spaCy NER
(модель ``uk_core_news_sm/md/lg``). Якщо spaCy недоступний — модуль не
ламається, просто ``extract_entities()`` повертає порожній список.

Схема БД:
  entities(id, name, kind, mentions, first_seen_book, summary)
  relations(id, src_entity, dst_entity, relation, weight, source_book)
  mentions(id, entity_id, source_book, chunk_idx, context)

Запити:
  ``find_entity("Кобзар")`` → точний/нечіткий пошук
  ``get_neighbors("Шевченко")`` → пов'язані сутності
  ``describe_entity(name)`` → опис + згадки + контексти
"""
from __future__ import annotations

import os
import re
import sqlite3
import logging
import threading
from typing import Iterable, List, Tuple, Optional, Dict, Any
from collections import Counter

logger = logging.getLogger(__name__)

# spaCy — опційно. Підвантажуємо ліниво.
_NLP = None
_NLP_LOCK = threading.Lock()
_NLP_TRIED = False


def _load_spacy() -> Optional[Any]:
    """Ліниве завантаження української моделі spaCy. Безпечно при відсутності."""
    global _NLP, _NLP_TRIED
    if _NLP is not None or _NLP_TRIED:
        return _NLP
    with _NLP_LOCK:
        if _NLP is not None or _NLP_TRIED:
            return _NLP
        _NLP_TRIED = True
        try:
            import spacy  # type: ignore
        except ImportError:
            logger.info("[kg] spaCy не встановлено — knowledge graph працює тільки на читання")
            return None
        for model in ("uk_core_news_lg", "uk_core_news_md", "uk_core_news_sm"):
            try:
                _NLP = spacy.load(model, disable=["parser", "lemmatizer"])
                logger.info(f"[kg] spaCy модель завантажена: {model}")
                return _NLP
            except OSError:
                continue
        logger.warning(
            "[kg] Українська spaCy-модель не знайдена. Встанови: "
            "python -m spacy download uk_core_news_md"
        )
        return None


# spaCy NER labels → наші типи
_LABEL_MAP = {
    "PER":  "person",
    "PERSON": "person",
    "LOC":  "location",
    "GPE":  "location",
    "ORG":  "organization",
    "EVENT": "event",
    "WORK_OF_ART": "work",
    "MISC": "misc",
}

# Мінімальна довжина імені, щоб уникнути сміття типу "Я", "Він".
_MIN_NAME_LEN = 3


def _normalize_name(name: str) -> str:
    """Нормалізація для дедуплікації: NFC, обрізання пунктуації, перші великі літери."""
    name = re.sub(r"\s+", " ", name).strip(" .,;:!?\"'«»()[]–—-")
    return name


# -------------------------- Сховище ----------------------------
class KnowledgeGraph:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        # check_same_thread=False + lock на запис — простий і безпечний підхід для домашнього асистента.
        return sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)

    def _init_db(self) -> None:
        with self._conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    name_norm TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    mentions INTEGER NOT NULL DEFAULT 0,
                    first_seen_book TEXT,
                    summary TEXT,
                    UNIQUE(name_norm, kind)
                );
                CREATE INDEX IF NOT EXISTS idx_entities_norm ON entities(name_norm);
                CREATE INDEX IF NOT EXISTS idx_entities_kind ON entities(kind);

                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_entity INTEGER NOT NULL,
                    dst_entity INTEGER NOT NULL,
                    relation TEXT NOT NULL DEFAULT 'co_mention',
                    weight INTEGER NOT NULL DEFAULT 1,
                    source_book TEXT,
                    UNIQUE(src_entity, dst_entity, relation, source_book),
                    FOREIGN KEY(src_entity) REFERENCES entities(id),
                    FOREIGN KEY(dst_entity) REFERENCES entities(id)
                );
                CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(src_entity);
                CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(dst_entity);

                CREATE TABLE IF NOT EXISTS mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    source_book TEXT,
                    chunk_idx INTEGER,
                    context TEXT,
                    FOREIGN KEY(entity_id) REFERENCES entities(id)
                );
                CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
                """
            )

    # --------- запис ---------
    def upsert_entity(self, name: str, kind: str, source_book: Optional[str]) -> Optional[int]:
        name_norm = _normalize_name(name).lower()
        if len(name_norm) < _MIN_NAME_LEN:
            return None
        with self._lock, self._conn() as con:
            cur = con.execute(
                "SELECT id, mentions FROM entities WHERE name_norm=? AND kind=?",
                (name_norm, kind),
            )
            row = cur.fetchone()
            if row:
                con.execute(
                    "UPDATE entities SET mentions = mentions + 1 WHERE id=?",
                    (row[0],),
                )
                return row[0]
            cur = con.execute(
                "INSERT INTO entities(name, name_norm, kind, mentions, first_seen_book) "
                "VALUES (?, ?, ?, 1, ?)",
                (_normalize_name(name), name_norm, kind, source_book),
            )
            return cur.lastrowid

    def add_mention(
        self,
        entity_id: int,
        source_book: Optional[str],
        chunk_idx: Optional[int],
        context: str,
        max_context_chars: int = 240,
    ) -> None:
        ctx = (context or "").strip()
        if len(ctx) > max_context_chars:
            ctx = ctx[: max_context_chars - 1] + "…"
        with self._lock, self._conn() as con:
            con.execute(
                "INSERT INTO mentions(entity_id, source_book, chunk_idx, context) VALUES (?,?,?,?)",
                (entity_id, source_book, chunk_idx, ctx),
            )

    def add_relation(
        self,
        src_id: int,
        dst_id: int,
        source_book: Optional[str],
        relation: str = "co_mention",
    ) -> None:
        if src_id == dst_id:
            return
        # Канонічний порядок — щоб (A,B) і (B,A) рахувалися одним зв'язком.
        a, b = sorted((src_id, dst_id))
        with self._lock, self._conn() as con:
            cur = con.execute(
                "SELECT id, weight FROM relations "
                "WHERE src_entity=? AND dst_entity=? AND relation=? AND COALESCE(source_book,'')=COALESCE(?,'')",
                (a, b, relation, source_book),
            )
            row = cur.fetchone()
            if row:
                con.execute(
                    "UPDATE relations SET weight = weight + 1 WHERE id=?",
                    (row[0],),
                )
            else:
                con.execute(
                    "INSERT INTO relations(src_entity, dst_entity, relation, weight, source_book) "
                    "VALUES (?,?,?,1,?)",
                    (a, b, relation, source_book),
                )

    # --------- читання ---------
    def find_entity(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        q = _normalize_name(query).lower()
        if len(q) < 2:
            return []
        with self._conn() as con:
            con.row_factory = sqlite3.Row
            # Точний → префіксний → LIKE.
            rows = con.execute(
                "SELECT * FROM entities WHERE name_norm=? "
                "UNION SELECT * FROM entities WHERE name_norm LIKE ? "
                "UNION SELECT * FROM entities WHERE name_norm LIKE ? "
                "ORDER BY mentions DESC LIMIT ?",
                (q, q + "%", "%" + q + "%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_neighbors(self, entity_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT e.*, r.weight, r.relation, r.source_book
                FROM relations r
                JOIN entities e ON e.id =
                    CASE WHEN r.src_entity=? THEN r.dst_entity ELSE r.src_entity END
                WHERE r.src_entity=? OR r.dst_entity=?
                ORDER BY r.weight DESC
                LIMIT ?
                """,
                (entity_id, entity_id, entity_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_mentions(self, entity_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        with self._conn() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM mentions WHERE entity_id=? ORDER BY id DESC LIMIT ?",
                (entity_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        with self._conn() as con:
            return {
                "entities":  con.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "relations": con.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
                "mentions":  con.execute("SELECT COUNT(*) FROM mentions").fetchone()[0],
            }


# -------------------------- NER-екстрактор ---------------------
def extract_entities(text: str, max_chars: int = 200_000) -> List[Tuple[str, str]]:
    """Повертає список (name, kind) для одного фрагмента/чанка.

    ``max_chars`` обмежує розмір тексту, переданого spaCy за один виклик
    (захист пам'яті — для дуже довгих книг краще викликати по чанках).
    """
    nlp = _load_spacy()
    if nlp is None:
        return []
    if not text:
        return []
    doc = nlp(text[:max_chars])
    out: List[Tuple[str, str]] = []
    for ent in doc.ents:
        kind = _LABEL_MAP.get(ent.label_)
        if not kind:
            continue
        name = _normalize_name(ent.text)
        if len(name) < _MIN_NAME_LEN:
            continue
        out.append((name, kind))
    return out


def ingest_chunk_into_kg(
    kg: KnowledgeGraph,
    chunk: str,
    source_book: Optional[str],
    chunk_idx: Optional[int],
    max_relations_per_chunk: int = 30,
) -> int:
    """Витягає сутності з одного чанка, реєструє згадки та co-mention зв'язки.

    Повертає кількість нових/оновлених сутностей.
    """
    pairs = extract_entities(chunk)
    if not pairs:
        return 0

    # Лічильник у межах чанка → беремо найчастіші, щоб не плодити шум.
    counter: Counter = Counter(pairs)
    top = [item for item, _ in counter.most_common(max_relations_per_chunk)]

    ids: List[int] = []
    for name, kind in top:
        eid = kg.upsert_entity(name, kind, source_book)
        if eid is None:
            continue
        kg.add_mention(eid, source_book, chunk_idx, chunk)
        ids.append(eid)

    # Co-mention зв'язки: усі пари сутностей у цьому чанку.
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            kg.add_relation(ids[i], ids[j], source_book, relation="co_mention")

    return len(ids)


# -------------------------- Singleton --------------------------
_KG_SINGLETON: Optional[KnowledgeGraph] = None
_KG_LOCK = threading.Lock()


def get_kg(db_path: Optional[str] = None) -> KnowledgeGraph:
    global _KG_SINGLETON
    if _KG_SINGLETON is not None:
        return _KG_SINGLETON
    with _KG_LOCK:
        if _KG_SINGLETON is None:
            try:
                import config as _cfg
                default_path = getattr(
                    _cfg, "KG_DB_PATH",
                    os.path.join(_cfg.BASE_DIR, "jarvis_kg", "graph.sqlite"),
                )
            except Exception:
                default_path = os.path.join(os.getcwd(), "jarvis_kg", "graph.sqlite")
            _KG_SINGLETON = KnowledgeGraph(db_path or default_path)
        return _KG_SINGLETON
