"""Audit Whisper dataset: durations, silence, missing files, suspicious texts, dups."""
import csv, os, sys, wave, contextlib, re
from collections import Counter

TSV = "my-dataset/train.tsv"
ROOT = "my-dataset"
SR_EXPECTED = 16000

if not os.path.exists(TSV):
    print(f"[!] {TSV} not found"); sys.exit(1)

rows = []
with open(TSV, encoding="utf-8") as f:
    reader = csv.reader(f, delimiter="\t")
    header = next(reader, None)
    for ln, parts in enumerate(reader, start=2):
        if len(parts) < 2:
            print(f"[BAD ROW {ln}] {parts}"); continue
        rows.append((ln, parts[0].strip(), parts[1].strip()))

print(f"[*] Header: {header}")
print(f"[*] Rows in TSV: {len(rows)}")

# --- find missing / dup paths
seen_paths = Counter(r[1] for r in rows)
dup_paths = [p for p, c in seen_paths.items() if c > 1]

missing, ok_files, files_meta = [], [], []
for ln, rel, txt in rows:
    full = os.path.join(ROOT, rel) if not rel.startswith(ROOT) else rel
    if not os.path.exists(full):
        missing.append((ln, rel)); continue
    try:
        with contextlib.closing(wave.open(full, "rb")) as w:
            sr = w.getframerate()
            n  = w.getnframes()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            dur = n / float(sr) if sr else 0.0
        files_meta.append((ln, rel, txt, sr, ch, sw, dur))
        ok_files.append(full)
    except Exception as e:
        missing.append((ln, rel + f" (read err: {e})"))

# --- orphan wavs (on disk but not in TSV)
audio_dir = os.path.join(ROOT, "audio1")
disk_files = []
if os.path.isdir(audio_dir):
    for fn in os.listdir(audio_dir):
        if fn.lower().endswith(".wav"):
            disk_files.append(f"audio1/{fn}")
tsv_set = set(r[1] for r in rows)
orphans = [f for f in disk_files if f not in tsv_set]

# --- duration stats
durs = [m[6] for m in files_meta]
short = [m for m in files_meta if m[6] < 0.4]
long_ = [m for m in files_meta if m[6] > 12.0]
wrong_sr = [m for m in files_meta if m[3] != SR_EXPECTED]
multi_ch = [m for m in files_meta if m[4] != 1]

# --- suspicious texts
empty_text = [m for m in files_meta if not m[2]]
weird = []
for m in files_meta:
    t = m[2]
    if len(t) < 3 or len(t) > 250:
        weird.append((m[0], m[1], f"len={len(t)}", t[:60])); continue
    # склеєні слова "Неопрааналізуємо"
    if re.search(r"[а-яіїєґА-ЯІЇЄҐ]{15,}", t):
        weird.append((m[0], m[1], "long-word", t[:80]))
    # три+ повтори літери
    elif re.search(r"(.)\1{3,}", t):
        weird.append((m[0], m[1], "repeat-char", t[:80]))

dup_texts = [(t, c) for t, c in Counter(m[2] for m in files_meta).items() if c > 1]

print("\n=== SUMMARY ===")
print(f"  total rows       : {len(rows)}")
print(f"  files ok         : {len(files_meta)}")
print(f"  missing/unread   : {len(missing)}")
print(f"  orphan wavs      : {len(orphans)}  (на диску, але не в tsv)")
print(f"  dup paths in tsv : {len(dup_paths)}")
print(f"  dup texts        : {len(dup_texts)}")
print(f"  wrong sample-rate: {len(wrong_sr)} (expected {SR_EXPECTED})")
print(f"  multi-channel    : {len(multi_ch)}")
print(f"  too short (<0.4s): {len(short)}")
print(f"  too long  (>12s) : {len(long_)}")
print(f"  empty text       : {len(empty_text)}")
print(f"  suspicious texts : {len(weird)}")
if durs:
    print(f"  duration min/avg/max: {min(durs):.2f}s / {sum(durs)/len(durs):.2f}s / {max(durs):.2f}s")
    print(f"  total audio time : {sum(durs)/60:.1f} min")

def show(title, items, fmt):
    if not items: return
    print(f"\n--- {title} ({len(items)}) ---")
    for it in items[:25]:
        print("  " + fmt(it))
    if len(items) > 25:
        print(f"  ... +{len(items)-25} more")

show("MISSING", missing, lambda x: f"line {x[0]}: {x[1]}")
show("ORPHANS", orphans, lambda x: x)
show("DUP PATHS", dup_paths, lambda x: x)
show("DUP TEXTS", dup_texts, lambda x: f"x{x[1]}: {x[0][:80]}")
show("WRONG SR", wrong_sr, lambda x: f"line {x[0]} sr={x[3]}: {x[1]}")
show("MULTI-CH", multi_ch, lambda x: f"line {x[0]} ch={x[4]}: {x[1]}")
show("TOO SHORT", short, lambda x: f"line {x[0]} {x[6]:.2f}s: {x[1]}  «{x[2][:40]}»")
show("TOO LONG",  long_, lambda x: f"line {x[0]} {x[6]:.2f}s: {x[1]}  «{x[2][:40]}»")
show("SUSPICIOUS TEXTS", weird, lambda x: f"line {x[0]} [{x[2]}]: {x[1]}  «{x[3]}»")
