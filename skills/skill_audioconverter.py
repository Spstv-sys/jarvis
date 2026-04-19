import os
from pathlib import Path
try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None

# Слова, на які відреагує Джарвіс
TRIGGERS = ["конвертуй аудіо", "зроби wav", "перетвори музику", "конвертуй mp3"]

def execute(cmd, jarvis):
    if AudioSegment is None:
        return "У мене немає бібліотеки pydub. Будь ласка, встановіть її."

    # Папка, де Джарвіс буде шукати файли для конвертації
    source_dir = Path.home() / "Downloads" 
    
    # Папка, куди він збереже результат
    target_dir = Path(os.getcwd()) / "Converted_Audio"
    target_dir.mkdir(exist_ok=True)

    # Попереджаємо, що починаємо роботу (щоб ви знали, що він не завис)
    jarvis.audio.speak("Шукаю MP3 файли у папці Завантаження...")

    # Шукаємо всі файли .mp3 у папці (не заходячи в підпапки)
    mp3_files = list(source_dir.glob("*.mp3"))

    if not mp3_files:
        return "Я не знайшов жодного MP3 файлу у ваших завантаженнях."

    jarvis.audio.speak(f"Знайдено {len(mp3_files)} файлів. Починаю конвертацію.")
    
    success_count = 0
    errors = 0

    for file_path in mp3_files:
        try:
            # Формуємо ім'я нового файлу
            output_file = target_dir / (file_path.stem + ".wav")
            
            # Конвертація
            audio = AudioSegment.from_mp3(file_path)
            audio.export(output_file, format="wav")
            success_count += 1
            
            # За бажанням: можна видаляти оригінальний mp3 після конвертації
            # file_path.unlink() 
            
        except Exception as e:
            print(f"Помилка конвертації файлу {file_path.name}: {e}")
            errors += 1

    # Відкриваємо папку з результатами
    try:
        if os.name == 'nt':
            os.startfile(target_dir)
    except Exception:
        pass

    # Резюме
    if errors == 0:
        return f"Успішно конвертовано {success_count} файлів у формат WAV."
    else:
        return f"Конвертацію завершено. Успішно: {success_count}. Помилок: {errors}."