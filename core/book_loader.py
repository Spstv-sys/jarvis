"""Завантаження книг (EPUB / FB2 / PDF / DOCX / TXT) з метаданими.

Ціль: повертати кортеж (full_text, metadata) — щоб ingestion-пайплайн міг
прикріпити автора/назву/мову до кожного чанка у векторній БД.
"""
from __future__ import annotations

import os
import re
import logging
from typing import Optional, Tuple, Dict, Any
from html import unescape
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# Опціональні залежності — імпортуємо ліниво, щоб не падати без них
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import docx  # python-docx
except ImportError:
    docx = None

try:
    from ebooklib import epub  # EPUB
    import ebooklib
except ImportError:
    epub = None
    ebooklib = None


SUPPORTED_EXTS = {".epub", ".fb2", ".pdf", ".docx", ".txt", ".md"}


def _strip_html(html: str) -> str:
    """Прибирає HTML-теги, повертає чистий текст."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _meta_from_filename(path: str) -> Dict[str, Any]:
    """Евристика: 'Автор - Назва.epub' → {author, title}."""
    name = os.path.splitext(os.path.basename(path))[0]
    parts = re.split(r"\s*[-—–]\s*", name, maxsplit=1)
    if len(parts) == 2:
        return {"author": parts[0].strip(), "title": parts[1].strip()}
    return {"title": name.strip()}


# -------------------------- EPUB --------------------------
def load_epub(path: str) -> Tuple[str, Dict[str, Any]]:
    if epub is None:
        raise RuntimeError("Потрібна бібліотека ebooklib (pip install EbookLib)")
    book = epub.read_epub(path)

    meta: Dict[str, Any] = {"format": "epub"}
    try:
        titles = book.get_metadata("DC", "title")
        if titles:
            meta["title"] = titles[0][0]
        authors = book.get_metadata("DC", "creator")
        if authors:
            meta["author"] = ", ".join(a[0] for a in authors)
        langs = book.get_metadata("DC", "language")
        if langs:
            meta["language"] = langs[0][0]
    except Exception as e:
        logger.warning(f"EPUB metadata read failed for {path}: {e}")

    parts = []
    for item in book.get_items():
        if ebooklib and item.get_type() == ebooklib.ITEM_DOCUMENT:
            try:
                parts.append(_strip_html(item.get_content().decode("utf-8", errors="ignore")))
            except Exception:
                continue
    text = "\n\n".join(p for p in parts if p)

    fallback = _meta_from_filename(path)
    for k, v in fallback.items():
        meta.setdefault(k, v)
    return text, meta


# -------------------------- FB2 ---------------------------
_FB2_NS = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0"}

def load_fb2(path: str) -> Tuple[str, Dict[str, Any]]:
    """FB2 — це XML. Парсимо без додаткових бібліотек."""
    with open(path, "rb") as f:
        data = f.read()
    # FB2 файли інколи в .zip — це FB2.zip; підтримуємо тільки чистий fb2
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise RuntimeError(f"FB2 parse error: {e}")

    # Namespace може бути або заданий, або порожній — обробляємо обидва
    def _find(elem, tag):
        for prefix in ("fb:", ""):
            found = elem.find(f".//{prefix}{tag}", _FB2_NS if prefix else {})
            if found is not None:
                return found
        return None

    def _findall(elem, tag):
        for prefix in ("fb:", ""):
            res = elem.findall(f".//{prefix}{tag}", _FB2_NS if prefix else {})
            if res:
                return res
        return []

    meta: Dict[str, Any] = {"format": "fb2"}
    title_info = _find(root, "title-info")
    if title_info is not None:
        t = _find(title_info, "book-title")
        if t is not None and t.text:
            meta["title"] = t.text.strip()
        authors = _findall(title_info, "author")
        names = []
        for a in authors:
            first = _find(a, "first-name")
            last  = _find(a, "last-name")
            full = " ".join(x.text.strip() for x in (first, last) if x is not None and x.text)
            if full:
                names.append(full)
        if names:
            meta["author"] = ", ".join(names)
        lang = _find(title_info, "lang")
        if lang is not None and lang.text:
            meta["language"] = lang.text.strip()

    bodies = _findall(root, "body")
    parts = []
    for b in bodies:
        # Беремо весь текст з усіх вкладених тегів
        parts.append(" ".join(t.strip() for t in b.itertext() if t and t.strip()))
    text = "\n\n".join(parts)

    fallback = _meta_from_filename(path)
    for k, v in fallback.items():
        meta.setdefault(k, v)
    return text, meta


# -------------------------- PDF ---------------------------
def load_pdf(path: str) -> Tuple[str, Dict[str, Any]]:
    if fitz is None:
        raise RuntimeError("Потрібна бібліотека PyMuPDF (pip install pymupdf)")
    doc = fitz.open(path)
    meta: Dict[str, Any] = {"format": "pdf"}
    try:
        info = doc.metadata or {}
        if info.get("title"):
            meta["title"] = info["title"]
        if info.get("author"):
            meta["author"] = info["author"]
        meta["pages"] = doc.page_count
    except Exception:
        pass

    parts = []
    for page in doc:
        try:
            parts.append(page.get_text())
        except Exception:
            continue
    doc.close()
    text = "\n".join(parts)

    fallback = _meta_from_filename(path)
    for k, v in fallback.items():
        meta.setdefault(k, v)
    return text, meta


# -------------------------- DOCX --------------------------
def load_docx(path: str) -> Tuple[str, Dict[str, Any]]:
    if docx is None:
        raise RuntimeError("Потрібна бібліотека python-docx")
    d = docx.Document(path)
    text = "\n".join(p.text for p in d.paragraphs if p.text)
    meta: Dict[str, Any] = {"format": "docx"}
    try:
        cp = d.core_properties
        if cp.title:  meta["title"]  = cp.title
        if cp.author: meta["author"] = cp.author
    except Exception:
        pass
    fallback = _meta_from_filename(path)
    for k, v in fallback.items():
        meta.setdefault(k, v)
    return text, meta


# -------------------------- TXT/MD ------------------------
def load_text(path: str) -> Tuple[str, Dict[str, Any]]:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    meta = {"format": os.path.splitext(path)[1].lstrip(".").lower() or "txt"}
    meta.update(_meta_from_filename(path))
    return text, meta


# -------------------------- Router ------------------------
_LOADERS = {
    ".epub": load_epub,
    ".fb2":  load_fb2,
    ".pdf":  load_pdf,
    ".docx": load_docx,
    ".txt":  load_text,
    ".md":   load_text,
}


def load_book(path: str) -> Tuple[str, Dict[str, Any]]:
    """Завантажує книгу та повертає (full_text, metadata).

    Метадані містять як мінімум 'format' та 'title' (із вмісту або імені файлу),
    і за можливості — 'author', 'language', 'pages'.
    """
    ext = os.path.splitext(path)[1].lower()
    loader = _LOADERS.get(ext)
    if loader is None:
        raise RuntimeError(f"Формат {ext} не підтримується")

    text, meta = loader(path)
    text = re.sub(r"\n{3,}", "\n\n", text or "")
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    meta["source_path"] = os.path.abspath(path)
    meta["filename"]    = os.path.basename(path)
    meta["chars"]       = len(text)
    return text, meta
