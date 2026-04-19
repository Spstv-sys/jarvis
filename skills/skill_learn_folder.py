import os
import re
from PIL import Image

# Блоки імпорту бібліотек
try: import fitz
except ImportError: fitz = None

try: 
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except ImportError: pytesseract = None

try: import docx
except ImportError: docx = None

# Словник ваших папок (як у попередній навичці)
MY_FOLDERS = {
    "секретн": r"H:\Sicrets",
}

TRIGGERS = ["вивчи папку", "прочитай всі файли", "завантаж папку", "засвой папку"]

def execute(cmd, jarvis):
    request = cmd.lower()

    # 1. Шукаємо цільову папку
    target_dir = None
    folder_name_spoken = ""
    
    for root_word, path in MY_FOLDERS.items():
        if root_word in request:
            target_dir = path
            folder_name_spoken = "папку " + root_word.capitalize()
            break

    if not target_dir:
        return "Яку саме папку мені потрібно вивчити? Я не знайшов її у своїх ключах доступу."

    if not os.path.exists(target_dir):
        return f"{folder_name_spoken} не знайдена на диску."

    # 2. Отримуємо список файлів
    all_items = os.listdir(target_dir)
    valid_extensions = ['pdf', 'docx', 'txt', 'md', 'csv', 'jpg', 'png']
    
    # Відбираємо лише файли з підтримуваними форматами
    files_to_read = [
        f for f in all_items 
        if os.path.isfile(os.path.join(target_dir, f)) and f.lower().split('.')[-1] in valid_extensions
    ]

    if not files_to_read:
        return f"У {folder_name_spoken} немає файлів, які я можу прочитати (текст, pdf, docx або фото)."

    # Запобіжник: щоб не "повісити" систему, обмежуємо до 5 файлів за один раз
    MAX_FILES = 5
    files_to_process = files_to_read[:MAX_FILES]
    skipped_count = len(files_to_read) - len(files_to_process)

    jarvis.audio.speak(f"Знайдено {len(files_to_read)} сумісних файлів. Починаю масове читання та векторизацію.")

    total_chunks_saved = 0
    successful_files = 0
    errors = []

    # 3. Цикл читання кожного файлу
    for filename in files_to_process:
        filepath = os.path.join(target_dir, filename)
        ext = filename.lower().split('.')[-1]
        full_text = ""

        try:
            # --- Маршрутизатор форматів ---
            if ext == 'pdf' and fitz:
                doc = fitz.open(filepath)
                for page_num in range(len(doc)):
                    page = doc.load_page(page_num)
                    page_text = page.get_text().strip()
                    if len(page_text) < 15 and pytesseract:
                        pix = page.get_pixmap(dpi=300)
                        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                        page_text = pytesseract.image_to_string(img, lang='ukr+eng')
                    full_text += page_text + "\n"
                doc.close()

            elif ext in ['jpg', 'png'] and pytesseract:
                img = Image.open(filepath)
                full_text = pytesseract.image_to_string(img, lang='ukr+eng')

            elif ext == 'docx' and docx:
                doc_obj = docx.Document(filepath)
                for para in doc_obj.paragraphs:
                    full_text += para.text + "\n"

            elif ext in ['txt', 'md', 'csv']:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    full_text = f.read()
            # -------------------------------

            if not full_text.strip():
                continue

            # Очищення та нарізка на чанки
            full_text = re.sub(r'\n+', '\n', full_text)
            full_text = re.sub(r'\s{2,}', ' ', full_text).strip()
            
            chunk_size = 600
            overlap = 100
            chunks = [full_text[i:i + chunk_size].strip() for i in range(0, len(full_text), chunk_size - overlap) if len(full_text[i:i + chunk_size].strip()) > 50]

            # Збереження у мозок
            for chunk in chunks:
                jarvis.brain.memorize(f"[Джерело: {filename}] {chunk}", source=f"batch_{filename}")
            
            total_chunks_saved += len(chunks)
            successful_files += 1

        except Exception as e:
            errors.append(f"{filename}: {str(e)}")

    # 4. Формуємо звіт
    report = f"Масове вивчення завершено. Успішно засвоєно файлів: {successful_files}. У пам'ять записано {total_chunks_saved} векторних фрагментів. "
    
    if skipped_count > 0:
        report += f"Через запобіжник перевантаження пропущено {skipped_count} файлів. "
    if errors:
        report += "Під час читання деяких файлів виникли помилки. "

    return report