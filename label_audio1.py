#!/usr/bin/env python3
"""Безпечне розмічення записів my-dataset/audio1 для до-навчання.

Позиційне зіставлення (файл №i = фраза №i) виявилось НЕнадійним: порядок
записів не збігається з порядком фраз у списку. Тому мітки беремо з
ФАКТИЧНОГО аудіо: транскрибуємо кожен файл (large-v3), а тоді фаззі-підказуємо
найближчу «канонічну» фразу зі списку, який ти записував.

КРОК 1 (розмітка, без змін train.tsv):
    python label_audio1.py
  -> my-dataset/audio1_manifest.tsv   (path<TAB>text — готово до злиття)
     my-dataset/audio1_review.tsv     (path, raw_stt, suggested, score, CHECK)
  Відкрий review, виправ рядки з міткою CHECK (низька впевненість) прямо в
  audio1_manifest.tsv.

КРОК 2 (злиття у train.tsv, з бекапом):
    python label_audio1.py --merge
"""
from __future__ import annotations

import os
import sys
import csv
import glob
import shutil
import argparse

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(BASE, "my-dataset")
AUDIO1 = os.path.join(DATASET, "audio1")
PHRASES = os.path.join(DATASET, "phrases_to_record_commands.txt")
MANIFEST = os.path.join(DATASET, "audio1_manifest.tsv")
REVIEW = os.path.join(DATASET, "audio1_review.tsv")
TRAIN = os.path.join(DATASET, "train.tsv")

SNAP_THRESHOLD = 86   # >= => підставляємо канонічну фразу; < => лишаємо raw + CHECK


def _intended_phrases() -> list[str]:
    if not os.path.exists(PHRASES):
        return []
    return [l.strip() for l in open(PHRASES, encoding="utf-8")
            if l.strip() and not l.strip().startswith("#")]


def do_merge() -> None:
    if not os.path.exists(MANIFEST):
        print(f"[!] Немає {MANIFEST}. Спершу запусти без --merge."); sys.exit(1)
    rows = [r for r in csv.reader(open(MANIFEST, encoding="utf-8"), delimiter="\t")]
    rows = [r for r in rows if len(r) >= 2 and r[0].strip().lower() != "path"]
    if not rows:
        print("[!] Маніфест порожній."); sys.exit(1)
    shutil.copy(TRAIN, TRAIN + ".bak")
    print(f"[*] Бекап: {TRAIN}.bak")
    with open(TRAIN, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        for path, text in ((r[0], r[1]) for r in rows):
            w.writerow([path, text])
    print(f"[*] Долито {len(rows)} рядків у {TRAIN}. Перевір: python audit_dataset.py")


def do_label() -> None:
    from faster_whisper import WhisperModel
    try:
        from rapidfuzz import process, fuzz
    except Exception:
        process = None

    files = sorted(glob.glob(os.path.join(AUDIO1, "*.wav")))
    if not files:
        print(f"[!] Немає wav у {AUDIO1}"); sys.exit(1)
    phrases = _intended_phrases()
    print(f"[*] Файлів: {len(files)}; еталонних фраз: {len(phrases)}")

    print("[*] Завантаження faster-whisper large-v3...")
    try:
        model = WhisperModel("large-v3", device="cuda", compute_type="float16")
    except Exception as e:
        print(f"[!] CUDA недоступна ({e}) — CPU int8 (повільніше)...")
        model = WhisperModel("large-v3", device="cpu", compute_type="int8")

    man = open(MANIFEST, "w", encoding="utf-8", newline="")
    rev = open(REVIEW, "w", encoding="utf-8", newline="")
    mw, rw = csv.writer(man, delimiter="\t"), csv.writer(rev, delimiter="\t")
    mw.writerow(["path", "text"])
    rw.writerow(["path", "raw_stt", "suggested", "score", "flag"])

    n_check = 0
    for i, wav in enumerate(files, 1):
        segs, _ = model.transcribe(wav, language="uk", beam_size=5,
                                   condition_on_previous_text=False)
        raw = "".join(s.text for s in segs).strip()
        suggested, score = raw, 0
        if process is not None and phrases:
            m = process.extractOne(raw, phrases, scorer=fuzz.WRatio)
            if m:
                suggested, score = m[0], int(m[1])
        if score >= SNAP_THRESHOLD:
            text, flag = suggested, ""
        else:
            text, flag = raw, "CHECK"
            n_check += 1
        rel = "audio1/" + os.path.basename(wav)
        mw.writerow([rel, text])
        rw.writerow([rel, raw, suggested, score, flag])
        print(f"  [{i}/{len(files)}] {os.path.basename(wav)}  score={score:>3}  '{raw[:50]}'")

    man.close(); rev.close()
    print(f"\n[*] Готово. Маніфест: {MANIFEST}")
    print(f"[*] На звірку:      {REVIEW}  (рядків CHECK: {n_check})")
    print("[*] Виправ CHECK-рядки в audio1_manifest.tsv, тоді: python label_audio1.py --merge")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="Долити audio1_manifest.tsv у train.tsv (з бекапом)")
    args = ap.parse_args()
    (do_merge if args.merge else do_label)()
