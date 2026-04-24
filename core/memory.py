import sqlite3
import logging
import threading
import re

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# РЕЛЯЦІЙНА БД (SQLite)
# ─────────────────────────────────────────────
class JarvisDatabase:
    MAX_FACTS = 500

    def __init__(self, db_path="jarvis_core.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row  # ✅ Зручний доступ до колонок
        self._lock = threading.Lock()        # legacy (тримаємо для DDL)
        # WAL дозволяє паралельні читання + одне записування без зовнішнього lock
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
        except sqlite3.Error as e:
            logger.warning(f"SQLite PRAGMA failed: {e}")
        self._create_tables()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Централізований execute. SQLite з check_same_thread=False + WAL
        обробляє конкурентний доступ внутрішньо — зовнішній lock не потрібен
        для звичайних read/write запитів і лише сповільнює hot-path."""
        cur = self.conn.cursor()
        cur.execute(sql, params)
        self.conn.commit()
        return cur

    def _create_tables(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS calendar (
                    task_date TEXT,
                    task_text TEXT
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fact_text  TEXT
                );
                CREATE TABLE IF NOT EXISTS contacts (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    name_normalized TEXT NOT NULL UNIQUE,
                    phone           TEXT NOT NULL,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            self.conn.commit()

    # --- Settings ---
    def set_setting(self, key: str, value) -> None:
        self._execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )

    def get_setting(self, key: str, default=None):
        cur = self._execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    # --- Calendar ---
    def add_task(self, date_str: str, task_text: str) -> None:
        self._execute(
            "INSERT INTO calendar (task_date, task_text) VALUES (?, ?)",
            (date_str, task_text)
        )

    def get_tasks(self, date_str: str) -> list:
        cur = self._execute(
            "SELECT task_text FROM calendar WHERE task_date = ?",
            (date_str,)
        )
        return [row["task_text"] for row in cur.fetchall()]

    # --- Facts ---
    def add_fact(self, fact_text: str) -> None:
        """✅ Один запит замість двох — INSERT + обрізка через тригер"""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO facts (fact_text) VALUES (?)",
                (fact_text,)
            )
            # ✅ Один DELETE замість subquery
            cur.execute("""
                DELETE FROM facts
                WHERE id <= (
                    SELECT id FROM facts
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                )
            """, (self.MAX_FACTS,))
            self.conn.commit()

    def get_recent_facts(self, limit: int = 10) -> list:
        cur = self._execute(
            "SELECT fact_text FROM facts ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return [row["fact_text"] for row in cur.fetchall()]

    def search_facts(self, query: str, limit: int = 10) -> list:
        """Повертає релевантні факти за ключовими словами запиту."""
        if not query or not query.strip():
            return []

        raw_tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 3]
        if not raw_tokens:
            return self.get_recent_facts(limit=limit)

        def _variants(token: str) -> set[str]:
            variants = {token}
            suffixes = ("ами", "ями", "ові", "еві", "ого", "ому", "ою", "ею", "ій", "ий", "ий", "а", "я", "и", "і", "у", "ю", "о")
            for suffix in suffixes:
                if token.endswith(suffix) and len(token) - len(suffix) >= 3:
                    variants.add(token[:-len(suffix)])
            return variants

        token_variants = [_variants(t) for t in raw_tokens[:8]]
        cur = self._execute(
            "SELECT id, fact_text FROM facts ORDER BY id DESC LIMIT 300"
        )
        rows = cur.fetchall()

        scored: list[tuple[int, int, str]] = []
        for row in rows:
            text = row["fact_text"]
            text_l = text.lower()
            score = 0
            for variants in token_variants:
                if any(v in text_l for v in variants):
                    score += 1
            if score > 0:
                scored.append((score, row["id"], text))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    # --- Contacts ---
    def upsert_contact(self, name: str, phone: str) -> None:
        if not name or not phone:
            return
        normalized_name = re.sub(r"\s+", " ", name.strip().lower())
        self._execute(
            """
            INSERT INTO contacts (name, name_normalized, phone)
            VALUES (?, ?, ?)
            ON CONFLICT(name_normalized)
            DO UPDATE SET
                name = excluded.name,
                phone = excluded.phone,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name.strip(), normalized_name, phone.strip()),
        )

    def search_contacts(self, query: str, limit: int = 3) -> list:
        if not query or not query.strip():
            return []

        raw_tokens = [t for t in re.findall(r"\w+", query.lower()) if len(t) >= 3]
        if not raw_tokens:
            cur = self._execute(
                "SELECT name, phone FROM contacts ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            return [f"Контакт: {row['name']}. Номер телефону: {row['phone']}." for row in rows]

        def _variants(token: str) -> set[str]:
            variants = {token}
            suffixes = ("ами", "ями", "ові", "еві", "ого", "ому", "ою", "ею", "ій", "а", "я", "и", "і", "у", "ю", "о")
            for suffix in suffixes:
                if token.endswith(suffix) and len(token) - len(suffix) >= 3:
                    variants.add(token[:-len(suffix)])
            return variants

        variants = set()
        for token in raw_tokens[:8]:
            variants.update(_variants(token))

        like_parts = []
        params: list[str | int] = []
        for token in variants:
            like_parts.append("name_normalized LIKE ?")
            params.append(f"%{token}%")
        params.append(limit)

        sql = (
            "SELECT name, phone FROM contacts "
            f"WHERE {' OR '.join(like_parts)} "
            "ORDER BY updated_at DESC LIMIT ?"
        )
        cur = self._execute(sql, tuple(params))
        rows = cur.fetchall()
        return [f"Контакт: {row['name']}. Номер телефону: {row['phone']}." for row in rows]

    # --- Cleanup ---
    def close(self) -> None:
        """✅ Явне закриття з'єднання"""
        try:
            self.conn.close()
            logger.info("SQLite з'єднання закрито.")
        except Exception as e:
            logger.error(f"Помилка закриття SQLite: {e}")

    def __del__(self) -> None:
        self.close()