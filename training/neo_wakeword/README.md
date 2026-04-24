# 🎙️ Neo Wake-Word Training

Train a custom **"Neo"** wake-word for J.A.R.V.I.S. using openWakeWord.

## TL;DR

```powershell
# 1. Separate venv (don't touch Jarvis venv!)
python -m venv .venv-train
.venv-train\Scripts\activate

# 2. Install (NVIDIA GPU):
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements-train.txt

# 3. Train (~30-90 min on GPU)
python train_neo.py

# 4. Deploy
copy output\neo.onnx ..\..\.venv\Lib\site-packages\openwakeword\resources\models\
# then in .env:  OWW_MODEL_NAME=neo
```

## Why "Neo"?

| Criterion          | Verdict                                          |
| ------------------ | ------------------------------------------------ |
| Length             | ⚠️ Borderline (1 syllable). 2+ is safer.        |
| Phonetic uniqueness| ✅ "N-E-O" stands out in speech spectra         |
| Common in speech?  | ✅ Almost never — low false-trigger risk        |

If you get too many false triggers, retrain with `TARGET_PHRASE = "Hey Neo"`
in `train_neo.py`.

## No GPU? — Use Google Colab (free T4)

1. Open [openWakeWord training notebook](https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training_simple.ipynb)
2. Set `target_word = "Neo"` in the first cell
3. Runtime → Change runtime type → **T4 GPU**
4. Runtime → Run all (~45-60 min)
5. Download `neo.onnx` and place it in
   `.venv\Lib\site-packages\openwakeword\resources\models\`

## Tuning after training

Edit [config.py](../../config.py) (line ~154):

```python
OWW_MODEL_NAME    = "neo"
OWW_VAD_THRESHOLD = 0.5    # 0.4 = sensitive, 0.7 = strict
```

| Symptom                              | Fix                              |
| ------------------------------------ | -------------------------------- |
| Triggers without you saying anything | Raise threshold to 0.6-0.7       |
| Doesn't react when you call         | Lower threshold to 0.4           |
| Many false-positives on similar words| Retrain with more negative data  |

## Disk usage

- Negative datasets (`fma_small`, `audioset_filtered`): **~12 GB**
- Positive synthetic clips: **~500 MB**
- Final `neo.onnx`: **~1.2 MB**

You can delete `training_data/` after a successful train.
