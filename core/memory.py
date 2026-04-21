import sqlite3
import logging
import threading

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# РЕЛЯЦІЙНА БД (SQLite)
# ─────────────────────────────────────────────
class JarvisDatabase:
    MAX_FACTS = 50

    def __init__(self, db_path="jarvis_core.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # ✅ Зручний доступ до колонок
        self._lock = threading.Lock()        # ✅ Потокобезпека
        self._create_tables()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """✅ Централізований execute — окремий cursor на кожен виклик.

        commit() викликається лише для записуючих операцій (не SELECT).
        """
        is_write = not sql.lstrip().upper().startswith("SELECT")
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            if is_write:
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