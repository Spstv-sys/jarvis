# core/speaker.py
import sys
import pyttsx3

def speak_text(text):
    engine = pyttsx3.init()
    engine.setProperty('rate', 170)
    
    # Шукаємо український голос
    voices = engine.getProperty('voices')
    for v in voices:
        if 'ukr' in v.name.lower() or 'ukrainian' in v.name.lower():
            engine.setProperty('voice', v.id)
            break
            
    engine.say(text)
    engine.runAndWait()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            # Читаємо текст з тимчасового файлу, шлях до якого передано аргументом
            with open(sys.argv[1], 'r', encoding='utf-8') as f:
                text_to_speak = f.read()
            speak_text(text_to_speak)
        except Exception as e:
            pass