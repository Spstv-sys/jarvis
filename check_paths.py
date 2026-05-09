import os
import pandas as pd

# 1. Читаємо ваш TSV
tsv_path = "my-dataset/train.tsv"
df = pd.read_csv(tsv_path, sep='\t')

print(f"[*] У файлі знайдено колонок: {df.columns.tolist()}")
first_file = df['path'].iloc[0]
print(f"[*] Перший файл у списку: {first_file}")

# 2. Перевіряємо різні варіанти шляхів
variants = [
    first_file,                                      # як написано
    os.path.join("my-dataset", first_file),           # у папці датасету
    os.path.join("my-dataset", "audio", first_file)   # у вкладеній папці audio
]

print("\n[*] Перевірка фізичної наявності:")
for v in variants:
    exists = os.path.exists(v)
    print(f"Шлях: {os.path.abspath(v)} -> {'ЗНАЙДЕНО' if exists else 'НЕМАЄ'}")