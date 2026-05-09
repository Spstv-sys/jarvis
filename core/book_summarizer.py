"""Ієрархічні саммарі книг для глибокого RAG.

Ідея: для довгої книги векторний пошук дає лише шматки. Саммарі додає
два додаткові рівні абстракції:

  1) chapter-summary — короткий опис кожного блоку (~3000 символів)
  2) book-summary    — мета-саммарі усіх chapter-summaries

Обидва рівні зберігаються у тій самій Chroma колекції з метаданими
``kind="summary_chapter" | "summary_book"``, тому LLM може цитувати
"Загальний огляд книги Х" або "У 3-му розділі йдеться про…".
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Розмір блоку, з якого робиться один chapter-summary.
# 3000 символів ≈ 500-700 слів — комфортно для будь-якої LLM.
CHAPTER_BLOCK_CHARS = 3000

# Скільки максимум блоків саммаризувати на книгу (захист від багатогодинних запусків).
MAX_BLOCKS_PER_BOOK = 40


_CHAPTER_PROMPT = (
    "Ти — літературний редактор. Стисло (4-6 речень, українською) опиши, "
    "про що йдеться у наведеному фрагменті книги. Збережи ключові імена, "
    "події та ідеї. БЕЗ вступів типу 'У цьому фрагменті'.\n\n"
    "Книга: {title}{author_part}\n\n"
    "ФРАГМЕНТ:\n{chunk}\n\n"
    "СТИСЛИЙ ОПИС:"
)

_BOOK_PROMPT = (
    "Ти — літературний редактор. Об'єднай наведені описи частин книги "
    "у цілісний огляд (8-12 речень, українською): головна ідея, сюжетна "
    "лінія, ключові персонажі, атмосфера. БЕЗ вступів.\n\n"
    "Книга: {title}{author_part}\n\n"
    "ОПИСИ ЧАСТИН:\n{joined}\n\n"
    "ЗАГАЛЬНИЙ ОГЛЯД:"
)


def _split_into_blocks(text: str, block_chars: int = CHAPTER_BLOCK_CHARS) -> List[str]:
    """Простий розрізач — по абзацах, до досягнення цільового розміру блоку."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    blocks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for p in paragraphs:
        if cur_len + len(p) > block_chars and cur:
            blocks.append("\n\n".join(cur))
            cur, cur_len = [p], len(p)
        else:
            cur.append(p)
            cur_len += len(p)
    if cur:
        blocks.append("\n\n".join(cur))
    return blocks


def summarize_book(
    text: str,
    metadata: Dict[str, Any],
    llm_call: Callable[[str], Optional[str]],
    memorize: Callable[..., None],
    max_blocks: int = MAX_BLOCKS_PER_BOOK,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Створює ієрархічні саммарі та зберігає їх у пам'ять.

    Args:
        text: повний текст книги.
        metadata: метадані книги (title, author, source_path, …).
        llm_call: функція ``str → Optional[str]`` для виклику LLM
                  (наприклад, ``lambda p: jarvis.llm.generate_response(p, is_system_call=True)``).
        memorize: ``jarvis.brain.memorize`` (з підтримкою ``metadata=``).
        max_blocks: ліміт блоків (для коротких книг — менше).
        progress: опціональний колбек прогресу (логування / TTS).

    Returns:
        ``{"chapter_summaries": int, "book_summary": bool, "skipped": int}``
    """
    title = metadata.get("title") or metadata.get("filename") or "Невідома книга"
    author = metadata.get("author")
    author_part = f" — {author}" if author else ""

    blocks = _split_into_blocks(text)
    skipped = max(0, len(blocks) - max_blocks)
    blocks = blocks[:max_blocks]

    if progress:
        progress(f"Саммаризую '{title}': {len(blocks)} частин")

    chapter_summaries: List[str] = []
    saved_chapters = 0

    for idx, block in enumerate(blocks, 1):
        prompt = _CHAPTER_PROMPT.format(
            title=title, author_part=author_part, chunk=block
        )
        try:
            summary = llm_call(prompt)
        except Exception as e:
            logger.error(f"[summarizer] LLM error on chapter {idx}: {e}")
            summary = None

        if not summary or len(summary.strip()) < 30:
            continue

        summary = summary.strip()
        chapter_summaries.append(summary)

        meta_chunk = {
            "kind":        "summary_chapter",
            "title":       title,
            "author":      author,
            "chapter_idx": idx,
            "source":      f"summary_{metadata.get('filename', title)}",
            "source_path": metadata.get("source_path"),
        }
        memorize(
            f"[{title}{author_part} — частина {idx}/{len(blocks)}] {summary}",
            source=meta_chunk["source"],
            metadata=meta_chunk,
        )
        saved_chapters += 1

        if progress and idx % 5 == 0:
            progress(f"  …{idx}/{len(blocks)}")

    book_summary_saved = False
    if chapter_summaries:
        joined = "\n\n".join(
            f"{i}. {s}" for i, s in enumerate(chapter_summaries, 1)
        )
        # Якщо joined занадто великий — обрізаємо (захист від overflow контексту LLM).
        if len(joined) > 12000:
            joined = joined[:12000] + "…"

        try:
            book_summary = llm_call(
                _BOOK_PROMPT.format(
                    title=title, author_part=author_part, joined=joined
                )
            )
        except Exception as e:
            logger.error(f"[summarizer] LLM error on book summary: {e}")
            book_summary = None

        if book_summary and len(book_summary.strip()) > 50:
            meta_book = {
                "kind":        "summary_book",
                "title":       title,
                "author":      author,
                "source":      f"summary_book_{metadata.get('filename', title)}",
                "source_path": metadata.get("source_path"),
            }
            memorize(
                f"[Огляд книги «{title}»{author_part}] {book_summary.strip()}",
                source=meta_book["source"],
                metadata=meta_book,
            )
            book_summary_saved = True

    return {
        "chapter_summaries": saved_chapters,
        "book_summary":      book_summary_saved,
        "skipped":           skipped,
    }
