import sys
from transformers import pipeline, AutoModelForSpeechSeq2Seq, AutoProcessor
import torch
import os

if len(sys.argv) < 3:
    print("Usage: python test_whisper_pipeline.py <model_dir> <audio_path>")
    sys.exit(1)

model_dir = sys.argv[1]
audio_path = sys.argv[2]

print(f"Loading processor and model from: {model_dir}")
processor = AutoProcessor.from_pretrained(model_dir)
model = AutoModelForSpeechSeq2Seq.from_pretrained(
    model_dir,
    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32
)
device = 0 if torch.cuda.is_available() else 'cpu'
pipe = pipeline(
    "automatic-speech-recognition",
    model=model,
    tokenizer=processor.tokenizer,
    feature_extractor=processor.feature_extractor,
    device=device,
    return_timestamps=True,
    chunk_length_s=30,
    stride_length_s=5,
)

print(f"Running inference on: {audio_path}")
result = pipe(audio_path, return_timestamps=True, generate_kwargs={"language": "uk"})
print("Result:")
print(result)
