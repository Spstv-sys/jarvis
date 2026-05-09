"""
Тест натренованої моделі на ЖИВОМУ голосі (не з тренувального сету).

Використання:
  python test_model_live.py              # інтерактивний режим (запис → транскрипція)
  python test_model_live.py --list-devices

У інтерактивному режимі:
  Enter             — почати запис (VAD авто-зупинка)
  <текст> Enter     — почати запис + порівняти з еталоном
  exit / q          — вийти
"""
import argparse
import os
import sys
import time
import numpy as np
import sounddevice as sd
import torch
from scipy.io.wavfile import write
from transformers import WhisperForConditionalGeneration, WhisperProcessor
from peft import PeftModel

BASE = "openai/whisper-small"
ADAPTER = "./whisper-final-model"
SAMPLE_RATE = 16000
SAVE_DIR = "live_test_recordings"


def record_vad(max_seconds=10.0, silence_seconds=1.0, rms_threshold=350) -> np.ndarray:
    chunk_ms = 100
    chunk_size = int(SAMPLE_RATE * chunk_ms / 1000)
    max_chunks = int(max_seconds * 1000 / chunk_ms)
    silence_needed = int(silence_seconds * 1000 / chunk_ms)
    print(f"  ● Говори (max {max_seconds:.0f}s, авто-стоп після {silence_seconds:.1f}s тиші)...")
    captured: list[np.ndarray] = []
    silence_run = 0
    started = False
    start_time = time.time()
    last_print = 0.0
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=chunk_size) as stream:
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
                if silence_run >= silence_needed:
                    break
            if not started and (now - start_time) > 8.0:
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


def transcribe(model, processor, audio_int16: np.ndarray, device, dtype, forced_ids) -> str:
    audio = audio_int16.astype(np.float32) / 32768.0
    inputs = processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt")
    feats = inputs.input_features.to(device).to(dtype)
    with torch.no_grad():
        ids = model.generate(feats, forced_decoder_ids=forced_ids, max_new_tokens=200)
    return processor.batch_decode(ids, skip_special_tokens=True)[0].strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--device", type=int, default=None)
    ap.add_argument("--rms", type=int, default=350)
    ap.add_argument("--silence", type=float, default=1.0)
    ap.add_argument("--max-seconds", type=float, default=10.0)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        print("\nДоступні input-пристрої:")
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                mark = " *" if i == default_in else "  "
                print(f"  [{i}]{mark} {dev['name']} (channels={dev['max_input_channels']})")
        return 0

    if args.device is not None:
        sd.default.device = (args.device, None)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    print(f"[*] Пристрій: {device}")
    print(f"[*] Завантаження базової моделі: {BASE}")
    processor = WhisperProcessor.from_pretrained(ADAPTER)
    base_model = WhisperForConditionalGeneration.from_pretrained(BASE, torch_dtype=dtype)
    print(f"[*] Підключення LoRA адаптера: {ADAPTER}")
    model = PeftModel.from_pretrained(base_model, ADAPTER)
    model.to(device).eval()
    forced_ids = processor.get_decoder_prompt_ids(language="uk", task="transcribe")

    if not args.no_save:
        os.makedirs(SAVE_DIR, exist_ok=True)

    print("\n" + "=" * 70)
    print("ЖИВИЙ ТЕСТ. Натисни Enter — почати запис. exit/q — вихід.")
    print("Можна перед Enter ввести еталонний текст для порівняння.")
    print("=" * 70 + "\n")

    n = 0
    matches = 0
    total_with_ref = 0
    while True:
        try:
            ref = input(f"[{n+1}] Еталон (або Enter для запису без еталону, q=exit) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if ref.lower() in ("exit", "q", "quit", "стоп", "вихід", "вихiд"):
            break

        audio = record_vad(args.max_seconds, args.silence, args.rms)
        if audio.size < int(0.3 * SAMPLE_RATE):
            print("  [!] Запис надто короткий, пропускаю.\n")
            continue

        dur = audio.size / SAMPLE_RATE
        peak = int(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        print(f"  [aud] dur={dur:.2f}s peak={peak} rms={rms:.0f}")

        t0 = time.time()
        pred = transcribe(model, processor, audio, device, dtype, forced_ids)
        t1 = time.time()
        print(f"  [pred] ({t1-t0:.2f}s) {pred}")

        if ref:
            total_with_ref += 1
            ok = pred.lower().strip(".,!?\"«»") == ref.lower().strip(".,!?\"«»")
            if ok:
                matches += 1
            print(f"  [ref ] {ref}")
            print(f"  [{'✓ MATCH' if ok else '✗ DIFF'}]")

        if not args.no_save:
            n += 1
            fp = os.path.join(SAVE_DIR, f"live_{int(time.time())}_{n:03d}.wav")
            write(fp, SAMPLE_RATE, audio)
            print(f"  [saved] {fp}")
        print()

    if total_with_ref:
        print(f"\nТочних співпадінь: {matches}/{total_with_ref}")
    print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
