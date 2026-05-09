"""
Безпечне додавання нових семплів у Whisper-датасет.

- НЕ затирає існуючий train.tsv (append-mode)
- Автоматично продовжує нумерацію audio_NNNN.wav з останнього файлу
- Команди в інтерактивному режимі:
    <текст> Enter   — записати новий семпл
    r       Enter   — перезаписати ОСТАННІЙ семпл (з тим же текстом)
    s / порожньо    — пропустити
    exit / Ctrl+C   — вийти
- Тривалість: VAD-автозупинка (default) або фіксована (--duration N)
"""

import argparse
import os
import re
import sys
import time
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write

SAMPLE_RATE = 16000
AUDIO_DIR = "my-dataset/audio"
TSV_PATH = "my-dataset/train.tsv"


def find_next_index(audio_dir: str) -> int:
    if not os.path.isdir(audio_dir):
        return 1
    pat = re.compile(r"^audio_(\d+)\.wav$", re.IGNORECASE)
    max_idx = 0
    for name in os.listdir(audio_dir):
        m = pat.match(name)
        if m:
            try:
                v = int(m.group(1))
                if v > max_idx:
                    max_idx = v
            except ValueError:
                pass
    return max_idx + 1


def ensure_tsv_header(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if (not os.path.exists(path)) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write("path\ttext\n")


def record_fixed(seconds: float) -> np.ndarray:
    print(f"  ● Запис {seconds:.1f}s ...")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
    )
    sd.wait()
    return audio.reshape(-1)


def record_vad(max_seconds=8.0, silence_seconds=0.9, rms_threshold=350) -> np.ndarray:
    chunk_ms = 100
    chunk_size = int(SAMPLE_RATE * chunk_ms / 1000)
    max_chunks = int(max_seconds * 1000 / chunk_ms)
    silence_chunks_needed = int(silence_seconds * 1000 / chunk_ms)

    print(
        f"  ● Запис (max {max_seconds:.1f}s, auto-stop після {silence_seconds:.1f}s тиші) ..."
    )

    captured: list[np.ndarray] = []
    silence_run = 0
    started = False
    start_time = time.time()
    last_print = 0.0

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=chunk_size
    ) as stream:
        for _ in range(max_chunks):
            block, _ = stream.read(chunk_size)
            block = block.reshape(-1)
            captured.append(block.copy())

            rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            now = time.time()
            if now - last_print > 0.2:
                bars = "#" * min(40, int(rms / 50))
                sys.stdout.write(f"\r    rms={rms:6.0f} |{bars:<40}|")
                sys.stdout.flush()
                last_print = now

            if rms > rms_threshold:
                started = True
                silence_run = 0
            elif started:
                silence_run += 1
                if silence_run >= silence_chunks_needed:
                    break

            if not started and (now - start_time) > 6.0:
                break

    sys.stdout.write("\n")
    if not captured:
        return np.zeros(0, dtype="int16")
    audio = np.concatenate(captured).astype("int16")

    if started and audio.size > int(0.2 * SAMPLE_RATE):
        env = np.abs(audio).astype(np.int32)
        thr = max(rms_threshold // 2, 100)
        idxs = np.where(env > thr)[0]
        if idxs.size:
            keep_tail = int(0.2 * SAMPLE_RATE)
            end = min(audio.size, int(idxs[-1]) + keep_tail)
            audio = audio[:end]
    return audio


def list_input_devices() -> None:
    print("\nДоступні input-пристрої:")
    default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            mark = " *" if i == default_in else "  "
            print(f"  [{i}]{mark} {dev['name']} (channels={dev['max_input_channels']})")


def _replace_tsv_line(tsv_path: str, rel_path: str, new_text: str) -> None:
    if not os.path.exists(tsv_path):
        return
    lines = []
    replaced = False
    with open(tsv_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0] == rel_path:
                lines.append(f"{rel_path}\t{new_text}\n")
                replaced = True
            else:
                lines.append(line)
    if not replaced:
        lines.append(f"{rel_path}\t{new_text}\n")
    with open(tsv_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Append voice samples to Whisper dataset")
    p.add_argument("--audio-dir", default=AUDIO_DIR)
    p.add_argument("--tsv", default=TSV_PATH)
    p.add_argument("--duration", type=float, default=0.0,
                   help="Фіксована тривалість, секунд (0 = VAD)")
    p.add_argument("--max-seconds", type=float, default=8.0)
    p.add_argument("--silence", type=float, default=0.9)
    p.add_argument("--rms", type=int, default=350, help="Поріг RMS для VAD (int16)")
    p.add_argument("--device", type=int, default=None, help="Index input-пристрою")
    p.add_argument("--list-devices", action="store_true")
    args = p.parse_args()

    if args.list_devices:
        list_input_devices()
        return 0

    if args.device is not None:
        cur = sd.default.device
        out_idx = cur[1] if isinstance(cur, (list, tuple)) else None
        sd.default.device = (args.device, out_idx)

    os.makedirs(args.audio_dir, exist_ok=True)
    ensure_tsv_header(args.tsv)

    next_idx = find_next_index(args.audio_dir)
    print(f"\n[*] Audio dir: {args.audio_dir}")
    print(f"[*] TSV:       {args.tsv}")
    print(f"[*] Стартую з audio_{next_idx:04d}.wav")
    print("[*] Команди: <текст>+Enter • 'r' повтор • 's'/Enter пропуск • 'exit' вихід\n")

    added = 0
    last_text: str | None = None
    last_path: str | None = None

    try:
        while True:
            try:
                text = input(f"#{next_idx:04d} > ").strip()
            except EOFError:
                break

            if text.lower() in ("exit", "quit", ":q"):
                break

            if text.lower() == "r":
                if last_text is None or last_path is None:
                    print("  (немає попереднього запису для повтору)")
                    continue
                text = last_text
                target = last_path
                used_idx = int(re.search(r"audio_(\d+)\.wav$", target).group(1))
            elif text == "" or text.lower() == "s":
                print("  (пропущено)")
                continue
            else:
                used_idx = next_idx
                target = os.path.join(args.audio_dir, f"audio_{used_idx:04d}.wav")

            sd.sleep(300)
            if args.duration > 0:
                audio = record_fixed(args.duration)
            else:
                audio = record_vad(
                    max_seconds=args.max_seconds,
                    silence_seconds=args.silence,
                    rms_threshold=args.rms,
                )

            if audio.size < int(0.3 * SAMPLE_RATE):
                print("  ⚠️  Запис надто короткий — пропускаю.")
                continue

            write(target, SAMPLE_RATE, audio)
            rel = f"audio/{os.path.basename(target)}"
            if used_idx == next_idx:
                with open(args.tsv, "a", encoding="utf-8") as tsv:
                    tsv.write(f"{rel}\t{text}\n")
                next_idx += 1
                added += 1
            else:
                _replace_tsv_line(args.tsv, rel, text)
                print("  ↻ перезаписано")

            duration_s = audio.size / SAMPLE_RATE
            print(f"  ✓ {rel}  ({duration_s:.2f}s)\n")
            last_text = text
            last_path = target
    except KeyboardInterrupt:
        print("\n[*] Перервано.")

    print(f"\n[+] Додано нових семплів: {added}")
    print(f"[+] Останній індекс: {next_idx - 1:04d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())