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

TRIGGERS = ["вивчи файл", "прочитай документ", "завантаж у базу", "додай у пам'ять", "прочитай pdf"]

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
        # Визначаємо розширення файлу (все в нижній регістр)
        ext = filepath.lower().split('.')[-1]

        # ---------------------------------------------------------
        # МАРШРУТИЗАТОР ФОРМАТІВ
        # ---------------------------------------------------------
        
        if ext == 'pdf':
            if fitz is None: return "Потрібна бібліотека PyMuPDF (fitz)."
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
            doc.close()

        elif ext in ['jpg', 'jpeg', 'png', 'bmp']:
            # Читаємо чисті картинки (фотографії документів)
            if pytesseract is None: return "Для читання фотографій потрібен Tesseract."
            img = Image.open(filepath)
            full_text = pytesseract.image_to_string(img, lang='ukr+eng')

        elif ext == 'docx':
            # Читаємо документи Word
            if docx is None: return "Потрібна бібліотека python-docx."
            doc_obj = docx.Document(filepath)
            for para in doc_obj.paragraphs:
                full_text += para.text + "\n"

        elif ext in ['txt', 'md', 'csv', 'py', 'json', 'log']:
            # Читаємо прості текстові формати
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                full_text = f.read()

        else:
            return f"Формат .{ext} наразі не підтримується для векторизації."

        # ---------------------------------------------------------

        if not full_text.strip():
            return "Файл прочитано, але тексту не знайдено."

        # Очищення та розбиття на чанки (як і раніше)
        full_text = re.sub(r'\n+', '\n', full_text)
        full_text = re.sub(r'\s{2,}', ' ', full_text).strip()

        chunk_size = 600
        overlap = 100
        chunks = []
        for i in range(0, len(full_text), chunk_size - overlap):
            chunk = full_text[i:i + chunk_size].strip()
            if len(chunk) > 50:
                chunks.append(chunk)

        filename = os.path.basename(filepath)
        for chunk in chunks:
            memory_block = f"[Джерело: {filename}] {chunk}"
            jarvis.brain.memorize(memory_block, source=f"doc_{filename}")

        return f"Успішно! Файл засвоєно та розбито на {len(chunks)} фрагментів пам'яті."

    except Exception as e:
        return f"Помилка під час читання: {e}"