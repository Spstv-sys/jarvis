#!/usr/bin/env python3
"""Оцінка якості Whisper для голосових команд Jarvis.

Порівнює до трьох варіантів STT на held-out наборі:
  * lora  — кастомний whisper-small + LoRA-адаптер (whisper-final-model), HF-pipeline;
  * turbo — faster-whisper "turbo" (CTranslate2);
  * base  — базовий openai/whisper-small без адаптера, HF-pipeline.

Метрики (на кожну модель):
  * WER  — word error rate (нижче = краще);
  * CER  — character error rate;
  * EXACT — частка повних збігів (після нормалізації);
  * ROUTE — частка семплів, де STT-помилка НЕ змінила маршрут команди
            (тобто route_key(гіпотеза) == route_key(еталон)). Це label-free
            проксі реальної надійності команд: навіть з ненульовим WER команда
            може стабільно потрапляти в той самий обробник.

Залежності моделей опційні: якщо peft/transformers/faster_whisper не
встановлені — відповідна модель просто пропускається з попередженням.

ПРИКЛАДИ
  python eval_whisper.py                       # авто: test.tsv або holdout з train.tsv
  python eval_whisper.py --models turbo,base   # лише дві моделі
  python eval_whisper.py --manifest my-dataset/test.tsv --limit 50
  python eval_whisper.py --holdout 40          # останні 40 рядків train.tsv як тест
"""
from __future__ import annotations

import os
import sys
import csv
import json
import time
import argparse
import logging
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("eval_whisper")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "my-dataset")
BASE_MODEL_ID = "openai/whisper-small"
LORA_DIR = os.path.join(BASE_DIR, "whisper-final-model")

# ===========================================================================
# Текстові метрики (самодостатні — без jiwer)
# ===========================================================================
import re

_PUNCT = re.compile(r"[^0-9a-zA-ZЀ-ӿʼ'’\s]+")
_WS = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Нижній регістр, без пунктуації, схлопнуті пробіли — спільна нормалізація."""
    if not s:
        return ""
    return _WS.sub(" ", _PUNCT.sub(" ", s.lower())).strip()


def _edit_distance(a: list, b: list) -> int:
    """Левенштейн на послідовностях (слова або символи)."""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


def wer(ref: str, hyp: str) -> float:
    r = normalize_text(ref).split()
    h = normalize_text(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    return _edit_distance(r, h) / len(r)


def cer(ref: str, hyp: str) -> float:
    r = list(normalize_text(ref).replace(" ", ""))
    h = list(normalize_text(hyp).replace(" ", ""))
    if not r:
        return 0.0 if not h else 1.0
    return _edit_distance(r, h) / len(r)


# ===========================================================================
# Маршрутизація: відтворює детермінований trigger-шар command_router.dispatch
# (dynamic skills / IntentRouter / LLM свідомо НЕ враховуються — вони
# залежать від рантайму; нас цікавить саме trigger-маршрут).
# ===========================================================================
from core import fuzzy_match
from core.asr_bias import command_prompt
try:
    from rapidfuzz import fuzz as _rf_fuzz
except Exception:  # pragma: no cover - rapidfuzz є залежністю проєкту
    _rf_fuzz = None

# Той самий порядок, що й у core/command_router.py:ROUTES.
ROUTE_ORDER = [
    "update_knowledge", "activate_skill", "learn_skill", "weather_exact",
    "weather", "youtube_play", "youtube_search", "news", "finance",
    "switch_ai", "personality", "check_ai", "timer", "hardware", "apps_os",
    "vision", "code_assistant", "calendar", "dictation", "system_exit",
    "stop_voice", "memorize", "web_search", "find_file",
]


def _trigger_in_cmd(trigger: str, cmd: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(trigger) + r"(?!\w)"
    return bool(re.search(pattern, cmd, re.IGNORECASE))


def route_key(text: str) -> str:
    """Повертає ключ маршруту (як у ROUTES) або 'chat', якщо тригер не зматчив.

    Дзеркалить пріоритети command_router: точний word-boundary → multi-word
    fuzzy (partial_ratio>=80, обидва багатослівні) → single-word fuzzy (гейти
    FUZZY_WORD_*). Збіг по цьому ключу = STT-помилка не зламала команду.
    """
    if not text:
        return "chat"
    for key in ROUTE_ORDER:
        triggers = config.TRIGGERS.get(key, [])
        for trigger in sorted(triggers, key=len, reverse=True):
            if _trigger_in_cmd(trigger, text):
                return key
            if (_rf_fuzz is not None
                    and len(trigger.split()) >= 2
                    and len(text.split()) >= 2
                    and _rf_fuzz.partial_ratio(text.lower(), trigger.lower()) >= 80):
                return key
            if (len(trigger.split()) == 1
                    and len(trigger) >= config.FUZZY_WORD_MIN_LEN
                    and (len(text.split()) <= config.FUZZY_WORD_MAX_CMD_WORDS
                         or len(trigger) >= config.FUZZY_WORD_LONG_LEN)
                    and fuzzy_match.matches(text, trigger, config.FUZZY_WORD_THRESHOLD)):
                return key
    return "chat"


# ===========================================================================
# Завантажувачі моделей (опційні залежності)
# ===========================================================================
def load_lora() -> Optional[Callable[[str], str]]:
    try:
        from transformers import pipeline, AutoModelForSpeechSeq2Seq, AutoProcessor
        from peft import PeftModel
        import torch
    except Exception as e:
        logger.warning(f"[lora] пропущено — немає залежностей: {e}")
        return None
    if not os.path.isdir(LORA_DIR):
        logger.warning(f"[lora] пропущено — немає {LORA_DIR}")
        return None
    try:
        proc = AutoProcessor.from_pretrained(BASE_MODEL_ID)
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        model = AutoModelForSpeechSeq2Seq.from_pretrained(BASE_MODEL_ID, dtype=dtype)
        model = PeftModel.from_pretrained(model, LORA_DIR)
        device = 0 if torch.cuda.is_available() else "cpu"
        pipe = pipeline(
            "automatic-speech-recognition", model=model,
            tokenizer=proc.tokenizer, feature_extractor=proc.feature_extractor,
            device=device, chunk_length_s=30,
        )

        gk = {"language": "ukrainian", "task": "transcribe"}
        prompt = command_prompt()
        pid = None
        if prompt:
            try:
                dev = next(pipe.model.parameters()).device
                pid = proc.get_prompt_ids(prompt, return_tensors="pt").to(dev)
            except Exception as e:
                logger.warning(f"[hf] prompt_ids build failed, біасинг вимкнено: {e}")
        _warned = {"done": False}

        def _t(path: str) -> str:
            if pid is not None:
                try:
                    r = pipe(path, generate_kwargs={**gk, "prompt_ids": pid})
                    return (r.get("text") if isinstance(r, dict) else "") or ""
                except Exception as e:
                    if not _warned["done"]:
                        logger.warning(f"[hf] prompt_ids НЕ застосовано ({type(e).__name__}: {e}) -> fallback без біасингу")
                        _warned["done"] = True
            r = pipe(path, generate_kwargs=gk)
            return (r.get("text") if isinstance(r, dict) else "") or ""
        return _t
    except Exception as e:
        logger.warning(f"[lora] помилка завантаження: {e}")
        return None


def load_base() -> Optional[Callable[[str], str]]:
    try:
        from transformers import pipeline, AutoModelForSpeechSeq2Seq, AutoProcessor
        import torch
    except Exception as e:
        logger.warning(f"[base] пропущено — немає залежностей: {e}")
        return None
    try:
        proc = AutoProcessor.from_pretrained(BASE_MODEL_ID)
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        model = AutoModelForSpeechSeq2Seq.from_pretrained(BASE_MODEL_ID, dtype=dtype)
        device = 0 if torch.cuda.is_available() else "cpu"
        pipe = pipeline(
            "automatic-speech-recognition", model=model,
            tokenizer=proc.tokenizer, feature_extractor=proc.feature_extractor,
            device=device, chunk_length_s=30,
        )

        gk = {"language": "ukrainian", "task": "transcribe"}
        prompt = command_prompt()
        pid = None
        if prompt:
            try:
                dev = next(pipe.model.parameters()).device
                pid = proc.get_prompt_ids(prompt, return_tensors="pt").to(dev)
            except Exception as e:
                logger.warning(f"[hf] prompt_ids build failed, біасинг вимкнено: {e}")
        _warned = {"done": False}

        def _t(path: str) -> str:
            if pid is not None:
                try:
                    r = pipe(path, generate_kwargs={**gk, "prompt_ids": pid})
                    return (r.get("text") if isinstance(r, dict) else "") or ""
                except Exception as e:
                    if not _warned["done"]:
                        logger.warning(f"[hf] prompt_ids НЕ застосовано ({type(e).__name__}: {e}) -> fallback без біасингу")
                        _warned["done"] = True
            r = pipe(path, generate_kwargs=gk)
            return (r.get("text") if isinstance(r, dict) else "") or ""
        return _t
    except Exception as e:
        logger.warning(f"[base] помилка завантаження: {e}")
        return None


def load_turbo() -> Optional[Callable[[str], str]]:
    try:
        from faster_whisper import WhisperModel
        import torch
        cuda = torch.cuda.is_available()
    except Exception:
        try:
            from faster_whisper import WhisperModel  # noqa: F811
            cuda = False
        except Exception as e:
            logger.warning(f"[turbo] пропущено — немає faster_whisper: {e}")
            return None
    try:
        if cuda:
            model = WhisperModel("turbo", device="cuda", compute_type="float16")
        else:
            model = WhisperModel("turbo", device="cpu", compute_type="int8")

        _prompt = command_prompt() or None

        def _t(path: str) -> str:
            segs, _ = model.transcribe(
                path, beam_size=1, best_of=1, language="uk",
                vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400),
                condition_on_previous_text=False, initial_prompt=_prompt,
            )
            return "".join(s.text for s in segs).strip()
        return _t
    except Exception as e:
        logger.warning(f"[turbo] помилка завантаження: {e}")
        return None


LOADERS = {"lora": load_lora, "turbo": load_turbo, "base": load_base}


# ===========================================================================
# Маніфест
# ===========================================================================
def load_manifest(path: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        # пропустити рядок-заголовок path/text
        if header and header[0].strip().lower() not in ("path", "audio", "file"):
            f.seek(0)
            reader = csv.reader(f, delimiter="\t")
        for r in reader:
            if len(r) < 2:
                continue
            rows.append((r[0].strip(), r[1].strip()))
    return rows


def resolve_audio(rel: str) -> str:
    if os.path.isabs(rel) and os.path.exists(rel):
        return rel
    for base in (DATASET_DIR, BASE_DIR):
        cand = os.path.join(base, rel)
        if os.path.exists(cand):
            return cand
    return rel


# ===========================================================================
# Головна оцінка
# ===========================================================================
def evaluate(model_name: str, transcribe: Callable[[str], str],
             samples: list[tuple[str, str]]) -> dict:
    n = len(samples)
    sum_wer = sum_cer = exact = route_ok = 0.0
    per_sample = []
    t0 = time.time()
    for i, (rel, ref) in enumerate(samples, 1):
        audio = resolve_audio(rel)
        if not os.path.exists(audio):
            logger.warning(f"[{model_name}] немає файлу: {audio}")
            continue
        try:
            hyp = transcribe(audio)
        except Exception as e:
            logger.warning(f"[{model_name}] помилка на {rel}: {e}")
            hyp = ""
        w, c = wer(ref, hyp), cer(ref, hyp)
        ex = 1.0 if normalize_text(ref) == normalize_text(hyp) else 0.0
        rk_ref, rk_hyp = route_key(ref), route_key(hyp)
        ro = 1.0 if rk_ref == rk_hyp else 0.0
        sum_wer += w; sum_cer += c; exact += ex; route_ok += ro
        per_sample.append({
            "path": rel, "ref": ref, "hyp": hyp,
            "wer": round(w, 4), "cer": round(c, 4),
            "route_ref": rk_ref, "route_hyp": rk_hyp, "route_ok": int(ro),
        })
        if i % 25 == 0:
            print(f"  [{model_name}] {i}/{n}...", flush=True)
    cnt = len(per_sample) or 1
    elapsed = time.time() - t0
    return {
        "model": model_name,
        "samples": len(per_sample),
        "WER": round(sum_wer / cnt, 4),
        "CER": round(sum_cer / cnt, 4),
        "EXACT": round(exact / cnt, 4),
        "ROUTE": round(route_ok / cnt, 4),
        "sec_per_sample": round(elapsed / cnt, 3),
        "_per_sample": per_sample,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Whisper WER/routing evaluation")
    ap.add_argument("--manifest", default=None, help="TSV: path<TAB>text (default: my-dataset/test.tsv або holdout з train.tsv)")
    ap.add_argument("--models", default="lora,turbo,base", help="Кома-список: lora,turbo,base")
    ap.add_argument("--holdout", type=int, default=40, help="Якщо немає test.tsv — стільки ОСТАННІХ рядків train.tsv як тест")
    ap.add_argument("--limit", type=int, default=0, help="Обмежити кількість семплів (0 = усі)")
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "whisper_eval_report"), help="Префікс для .json/.csv звіту")
    args = ap.parse_args()

    # --- маніфест ---
    optimistic = False
    if args.manifest:
        manifest_path = args.manifest
        samples = load_manifest(manifest_path)
    else:
        test_tsv = os.path.join(DATASET_DIR, "test.tsv")
        if os.path.exists(test_tsv):
            manifest_path = test_tsv
            samples = load_manifest(manifest_path)
        else:
            manifest_path = os.path.join(DATASET_DIR, "train.tsv")
            all_rows = load_manifest(manifest_path)
            samples = all_rows[-args.holdout:] if args.holdout > 0 else all_rows
            optimistic = True
            print(f"[!] test.tsv не знайдено — беру останні {len(samples)} рядків train.tsv як holdout.")
            print("[!] УВАГА: ці семпли могли бути у тренуванні → метрики ОПТИМІСТИЧНІ.")

    if args.limit > 0:
        samples = samples[: args.limit]
    if not samples:
        print(f"[!] Порожній маніфест: {manifest_path}")
        sys.exit(1)
    print(f"[*] Маніфест: {manifest_path} ({len(samples)} семплів)")

    requested = [m.strip() for m in args.models.split(",") if m.strip()]
    results = []
    for name in requested:
        loader = LOADERS.get(name)
        if loader is None:
            print(f"[!] Невідома модель '{name}', пропускаю.")
            continue
        print(f"\n[*] Завантаження моделі: {name} ...")
        transcribe = loader()
        if transcribe is None:
            print(f"[!] Модель '{name}' недоступна — пропущено.")
            continue
        print(f"[*] Оцінка '{name}' на {len(samples)} семплах...")
        res = evaluate(name, transcribe, samples)
        results.append(res)

    if not results:
        print("\n[!] Жодну модель не вдалося оцінити (перевір залежності: transformers/peft/faster-whisper).")
        sys.exit(1)

    # --- звіт ---
    print("\n" + "=" * 64)
    print(f"{'MODEL':<8} {'N':>4} {'WER':>8} {'CER':>8} {'EXACT':>8} {'ROUTE':>8} {'s/smp':>7}")
    print("-" * 64)
    for r in sorted(results, key=lambda x: x["WER"]):
        print(f"{r['model']:<8} {r['samples']:>4} {r['WER']:>8.3f} {r['CER']:>8.3f} "
              f"{r['EXACT']:>8.3f} {r['ROUTE']:>8.3f} {r['sec_per_sample']:>7.2f}")
    print("=" * 64)
    print("WER/CER нижче = краще. EXACT/ROUTE вище = краще. ROUTE — стабільність маршруту команди.")
    if optimistic:
        print("[!] Нагадування: holdout із train.tsv → реальні цифри на нових голосах будуть гіршими.")

    json_path = args.out + ".json"
    csv_path = args.out + ".csv"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"manifest": manifest_path, "optimistic": optimistic,
                   "summary": [{k: v for k, v in r.items() if k != "_per_sample"} for r in results]},
                  f, ensure_ascii=False, indent=2)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "path", "ref", "hyp", "wer", "cer", "route_ref", "route_hyp", "route_ok"])
        for r in results:
            for s in r["_per_sample"]:
                w.writerow([r["model"], s["path"], s["ref"], s["hyp"], s["wer"], s["cer"],
                            s["route_ref"], s["route_hyp"], s["route_ok"]])
    print(f"\n[*] Звіт збережено: {json_path}\n              {csv_path}")


if __name__ == "__main__":
    main()
