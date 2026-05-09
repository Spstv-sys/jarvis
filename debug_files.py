import os
import pandas as pd

# Шляхи
tsv_path = "my-dataset/train.tsv"
dataset_dir = "my-dataset"

if not os.path.exists(tsv_path):
    print(f"[-] Не знайдено TSV файл за адресою: {os.path.abspath(tsv_path)}")
else:
    df = pd.read_csv(tsv_path, sep='\t')
    first_rel_path = df['path'].iloc[0]
    
    print(f"[*] У файлі TSV записано шлях: {first_rel_path}")
    
    # Де ми реально шукаємо
    full_path = os.path.abspath(os.path.join(dataset_dir, first_rel_path))
    print(f"[*] Спроба знайти файл за адресою: {full_path}")
    
    if os.path.exists(full_path):
        print("[+] ФАЙЛ ЗНАЙДЕНО! Проблема в чомусь іншому.")
    else:
        print("[-] ФАЙЛ НЕ ЗНАЙДЕНО. Перевірте назву папки або розширення файлу.")
        # Виведемо список файлів у папці для допомоги
        audio_folder = os.path.join(dataset_dir, "audio")
        if os.path.exists(audio_folder):
            print(f"[*] Файли, які реально є в {audio_folder}:")
            print(os.listdir(audio_folder)[:5]) # перші 5 файлів