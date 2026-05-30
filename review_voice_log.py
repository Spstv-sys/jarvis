#!/usr/bin/env python3
"""Перегляд журналу голосових команд (Фаза 6) і доливання виправлень у датасет.

КРОК 1 — згенерувати таблицю на звірку:
    python review_voice_log.py
  -> voice_logs/review.tsv  (колонки: wav, recognized, route, correct)
  Відкрий її. Для кожного рядка:
    • якщо розпізнано ВІРНО і хочеш додати в навчання — лиши 'correct' як є;
    • якщо НЕВІРНО — впиши правильний текст у колонку 'correct';
    • якщо НЕ хочеш додавати цей кліп — ОЧИСТИ колонку 'correct' (порожньо).

КРОК 2 — долити обрані у train.tsv (з бекапом):
    python review_voice_log.py --merge
  Копіює відповідні wav у my-dataset/audio_live/ і додає рядки в train.tsv.
"""
from __future__ import annotations

import os
import sys
import csv
import json
import glob
import shutil
import argparse

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
import config
LOG_DIR = config.VOICE_LOG_DIR if os.path.isabs(config.VOICE_LOG_DIR) else os.path.join(BASE, config.VOICE_LOG_DIR)
INDEX = os.path.join(LOG_DIR, "index.jsonl")
REVIEW = os.path.join(LOG_DIR, "review.tsv")
DATASET = os.path.join(BASE, "my-dataset")
LIVE_DIR = os.path.join(DATASET, "audio_live")
TRAIN = os.path.join(DATASET, "train.tsv")

try:
    from eval_whisper import route_key
except Exception:
    def route_key(_):
        return "?"


def build_review() -> None:
    if not os.path.exists(INDEX):
        print(f"[!] Журнал порожній: {INDEX}"); sys.exit(1)
    rows = []
    for line in open(INDEX, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    if not rows:
        print("[!] Немає записів."); sys.exit(1)
    with open(REVIEW, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["wav", "recognized", "route", "correct"])
        for r in rows:
            txt = r.get("text", "")
            w.writerow([r.get("wav", ""), txt, route_key(txt), txt])
    print(f"[*] {len(rows)} записів -> {REVIEW}")
    print("[*] Відредагуй колонку 'correct' (порожньо = не додавати), тоді: python review_voice_log.py --merge")


def do_merge() -> None:
    if not os.path.exists(REVIEW):
        print(f"[!] Немає {REVIEW}. Спершу запусти без --merge."); sys.exit(1)
    rows = list(csv.DictReader(open(REVIEW, encoding="utf-8"), delimiter="\t"))
    chosen = [r for r in rows if (r.get("correct") or "").strip() and r.get("wav")]
    if not chosen:
        print("[!] Жодного рядка з заповненим 'correct'."); sys.exit(1)
    os.makedirs(LIVE_DIR, exist_ok=True)
    if os.path.exists(TRAIN):
        shutil.copy(TRAIN, TRAIN + ".bak")
        print(f"[*] Бекап: {TRAIN}.bak")
    added = 0
    with open(TRAIN, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        for r in chosen:
            src = os.path.join(LOG_DIR, r["wav"])
            if not os.path.exists(src):
                print(f"  [skip] немає {src}"); continue
            dst_name = r["wav"]
            shutil.copy(src, os.path.join(LIVE_DIR, dst_name))
            w.writerow([f"audio_live/{dst_name}", r["correct"].strip()])
            added += 1
    print(f"[*] Долито {added} рядків у {TRAIN} (аудіо -> my-dataset/audio_live/).")
    print("[*] Далі: python audit_dataset.py  →  python train_whisper_v2.py  →  python eval_whisper.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="Долити обрані виправлення у train.tsv")
    args = ap.parse_args()
    (do_merge if args.merge else build_review)()
