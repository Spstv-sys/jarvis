import os
import re
from PIL import Image

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except ImportError:
    pytesseract = None

try:
    import docx  # python-docx
except ImportError:
    docx = None

try:
    from core.book_loader import load_book, SUPPORTED_EXTS
except Exception:
    load_book = None
    SUPPORTED_EXTS = set()

TRIGGERS = ["вивчи файл", "прочитай документ", "завантаж у базу", "додай у пам'ять", "прочитай pdf", "прочитай книгу"]

def execute(cmd, jarvis):
    filepath = cmd
    for t in TRIGGERS:
        filepath = filepath.replace(t, "")
    filepath = filepath.strip()

    if not filepath or not os.path.exists(filepath):
        return f"Я не можу знайти файл за шляхом: {filepath}."

    jarvis.audio.speak(f"Починаю аналіз файлу {os.path.basename(filepath)}...")

    try:
        full_text = ""
        ext_dot = os.path.splitext(filepath)[1].lower()
        ext = ext_dot.lstrip(".")
        meta = {}

        # ---------------------------------------------------------
        # EPUB / FB2 — через універсальний book_loader (з метаданими)
        # ---------------------------------------------------------
        if ext_dot in {".epub", ".fb2"} and load_book is not None:
            full_text, meta = load_book(filepath)

        # ---------------------------------------------------------
        # PDF — окремо, бо потребує OCR-фолбек для сканів
        # ---------------------------------------------------------
        elif ext == 'pdf':
            if fitz is None:
                return "Потрібна бібліотека PyMuPDF (fitz)."
            doc = fitz.open(filepath)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                page_text = page.get_text().strip()

                # OCR для сканованих сторінок PDF
                if len(page_text) < 15 and pytesseract:
                    pix = page.get_pixmap(dpi=300)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    page_text = pytesseract.image_to_string(img, lang='ukr+eng')

                full_text += page_text + "\n"
            try:
                info = doc.metadata or {}
                if info.get("title"):  meta["title"]  = info["title"]
                if info.get("author"): meta["author"] = info["author"]
            except Exception:
                pass
            doc.close()

        elif ext in ('jpg', 'jpeg', 'png', 'bmp'):
            if pytesseract is None:
                return "Для читання фотографій потрібен Tesseract."
            img = Image.open(filepath)
            full_text = pytesseract.image_to_string(img, lang='ukr+eng')

        elif ext == 'docx':
            if docx is None:
                return "Потрібна бібліотека python-docx."
            doc_obj = docx.Document(filepath)
            for para in doc_obj.paragraphs:
                full_text += para.text + "\n"

        elif ext in ('txt', 'md', 'csv', 'py', 'json', 'log'):
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                full_text = f.read()

        else:
            return f"Формат .{ext} наразі не підтримується для векторизації."

        # ---------------------------------------------------------

        if not full_text.strip():
            return "Файл прочитано, але тексту не знайдено."

        # Очищення та розбиття на чанки
        full_text = re.sub(r'\n{3,}', '\n\n', full_text)
        full_text = re.sub(r'[ \t]{2,}', ' ', full_text).strip()

        # Для книг (EPUB/FB2/PDF/DOCX) використовуємо більший чанк
        is_book = ext_dot in {".epub", ".fb2", ".pdf", ".docx"}
        chunk_size = 1500 if is_book else 600
        overlap    = 300  if is_book else 100

        chunks = []
        for i in range(0, len(full_text), chunk_size - overlap):
            chunk = full_text[i:i + chunk_size].strip()
            if len(chunk) > 50:
                chunks.append(chunk)

        filename = os.path.basename(filepath)
        title  = meta.get("title")  or os.path.splitext(filename)[0]
        author = meta.get("author")
        prefix_label = f"{title}" + (f" — {author}" if author else "")

        for i, chunk in enumerate(chunks):
            memory_block = f"[{prefix_label}] {chunk}"
            jarvis.brain.memorize(
                memory_block,
                source=f"doc_{filename}",
                metadata={
                    "title":       title,
                    "author":      author,
                    "format":      ext or meta.get("format"),
                    "source_path": os.path.abspath(filepath),
                    "chunk_idx":   i,
                },
            )

        author_str = f" автора {author}" if author else ""
        return f"Успішно! '{title}'{author_str} засвоєно: {len(chunks)} фрагментів пам'яті."

    except Exception as e:
        return f"Помилка під час читання: {e}"