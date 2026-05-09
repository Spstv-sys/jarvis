"""Навичка: масова індексація літератури (EPUB / FB2 / PDF / DOCX / TXT) з метаданими.

Тригери: "вивчи бібліотеку", "прочитай книгу", "засвой літературу", "вивчи книги".

Шлях до бібліотеки беремо з config.LITERATURE_DIR (за замовчуванням <project>/literature).
Кожен чанк зберігається з метаданими {author, title, format, chapter_idx, source_path},
що дозволяє RAG посилатись "Згідно з 'Кобзарем' Шевченка...".
"""
from __future__ import annotations

import os
import logging
from typing import List

import config
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.book_loader import load_book, SUPPORTED_EXTS
from core.book_summarizer import summarize_book

# Knowledge graph — опційно (вимикається через config.KG_ENABLED).
try:
    from core.knowledge_graph import get_kg, ingest_chunk_into_kg
    _KG_AVAILABLE = True
except Exception:
    _KG_AVAILABLE = False

logger = logging.getLogger(__name__)

TRIGGERS = [
    "вивчи бібліотеку",
    "вивчи книги",
    "прочитай книгу",
    "засвой літературу",
    "проіндексуй літературу",
    "вивчи бібліотеку глибоко",  # → з ієрархічними саммарі
    "глибоко вивчи бібліотеку",
]

# Тригери, які додатково запускають LLM-саммаризацію кожної книги.
DEEP_TRIGGERS = ("глибоко",)

# Захисний максимум — щоб одна команда не індексувала тисячі файлів безконтрольно.
MAX_BOOKS_PER_RUN = int(os.getenv("LIBRARY_MAX_BOOKS", "20"))


def _split_for_prose(text: str) -> List[str]:
    """Розбиття великих прозових текстів зі збереженням абзаців."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=getattr(config, "LITERATURE_CHUNK_SIZE", 1500),
        chunk_overlap=getattr(config, "LITERATURE_CHUNK_OVERLAP", 300),
        separators=["\n\n", "\n", ". ", "! ", "? ", " "],
    )
    return [c.strip() for c in splitter.split_text(text) if len(c.strip()) > 80]


def _resolve_target(cmd: str) -> str:
    """Дозволяємо вказати папку у команді: 'вивчи бібліотеку D:\\Books'."""
    request = cmd.strip()
    # Довші тригери — спершу, щоб не залишився "хвіст" типу "глибоко".
    for t in sorted(TRIGGERS, key=len, reverse=True):
        request = request.replace(t, "")
    # Окремо вирізаємо ключове слово deep-режиму, якщо воно стояло поза тригером.
    for kw in DEEP_TRIGGERS:
        request = request.replace(kw, "")
    request = request.strip(" :,.\"'")
    if request and os.path.isdir(request):
        return request
    return getattr(config, "LITERATURE_DIR", os.path.join(config.BASE_DIR, "literature"))


def _collect_books(folder: str) -> List[str]:
    found: List[str] = []
    for root, _, files in os.walk(folder):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in SUPPORTED_EXTS:
                found.append(os.path.join(root, fn))
    return found


def execute(cmd, jarvis):
    target = _resolve_target(cmd)
    deep_mode = any(t in cmd.lower() for t in DEEP_TRIGGERS)

    if not os.path.isdir(target):
        # Створюємо порожню папку, щоб користувач знав куди класти книги
        try:
            os.makedirs(target, exist_ok=True)
            return (
                f"Папку для бібліотеки створено: {target}. "
                "Поклади туди книжки у форматі EPUB / FB2 / PDF / DOCX / TXT і скажи знов."
            )
        except OSError as e:
            return f"Не можу створити папку бібліотеки: {e}"

    books = _collect_books(target)
    if not books:
        return (
            f"У бібліотеці {target} не знайдено книг. "
            "Підтримую формати: EPUB, FB2, PDF, DOCX, TXT, MD."
        )

    skipped = max(0, len(books) - MAX_BOOKS_PER_RUN)
    books = books[:MAX_BOOKS_PER_RUN]

    jarvis.audio.speak(
        f"Знайдено {len(books)} книг у бібліотеці. Починаю індексацію."
    )

    total_chunks = 0
    success = 0
    errors: List[str] = []

    # Готуємо KG, якщо ввімкнено.
    kg = None
    if _KG_AVAILABLE and getattr(config, "KG_ENABLED", False):
        try:
            kg = get_kg()
        except Exception as e:
            logger.error(f"[library] не вдалося відкрити KG: {e}")

    kg_entities = 0

    for idx, path in enumerate(books, 1):
        fname = os.path.basename(path)
        try:
            text, meta = load_book(path)
            if not text or len(text) < 200:
                errors.append(f"{fname}: порожній або занадто короткий")
                continue

            chunks = _split_for_prose(text)
            if not chunks:
                errors.append(f"{fname}: не вдалося розбити на чанки")
                continue

            base_meta = {
                "author":      meta.get("author"),
                "title":       meta.get("title", os.path.splitext(fname)[0]),
                "format":      meta.get("format"),
                "language":    meta.get("language"),
                "source":      f"book_{fname}",
                "source_path": meta.get("source_path", path),
            }

            # Додаємо мітку чанка по порядку — як сурогат "розділу"
            saved_for_book = 0
            for i, chunk in enumerate(chunks):
                meta_for_chunk = dict(base_meta)
                meta_for_chunk["chunk_idx"] = i
                # Префікс у тексті — щоб LLM бачив контекст навіть без metadata
                prefixed = (
                    f"[{base_meta['title']}"
                    + (f" — {base_meta['author']}" if base_meta.get("author") else "")
                    + f"] {chunk}"
                )
                jarvis.brain.memorize(
                    prefixed,
                    source=base_meta["source"],
                    metadata=meta_for_chunk,
                )
                saved_for_book += 1

                # Витягання сутностей у Knowledge Graph.
                if kg is not None:
                    try:
                        kg_entities += ingest_chunk_into_kg(
                            kg, chunk, source_book=fname, chunk_idx=i
                        )
                    except Exception as e:
                        logger.warning(f"[library] KG помилка на чанку {i} ({fname}): {e}")

            total_chunks += saved_for_book
            success += 1
            logger.info(
                f"[library] {idx}/{len(books)} '{base_meta['title']}' → {saved_for_book} чанків"
            )

            # Опційно: ієрархічні саммарі через LLM
            if deep_mode and getattr(jarvis, "llm", None) is not None:
                try:
                    res = summarize_book(
                        text=text,
                        metadata={**base_meta, "filename": fname},
                        llm_call=lambda p: jarvis.llm.generate_response(p, is_system_call=True),
                        memorize=jarvis.brain.memorize,
                        progress=lambda msg: logger.info(f"[summarizer] {msg}"),
                    )
                    logger.info(
                        f"[library] саммарі '{base_meta['title']}': "
                        f"{res['chapter_summaries']} розділів, "
                        f"огляд={'так' if res['book_summary'] else 'ні'}"
                    )
                except Exception as e:
                    logger.error(f"[library] помилка саммаризації {fname}: {e}")
                    errors.append(f"{fname} (саммарі): {e}")

        except Exception as e:
            logger.error(f"[library] помилка з {fname}: {e}")
            errors.append(f"{fname}: {e}")

    parts = [
        f"Індексацію завершено. Опрацьовано {success} з {len(books)} книг, "
        f"збережено {total_chunks} фрагментів пам'яті."
    ]
    if deep_mode:
        parts.append("Виконано глибоку саммаризацію через LLM.")
    if kg is not None:
        parts.append(f"Граф сутностей оновлено ({kg_entities} згадок).")
    if skipped:
        parts.append(f"Пропущено {skipped} книг через ліміт за один прогін.")
    if errors:
        parts.append(f"Помилок: {len(errors)}.")
    return " ".join(parts)
