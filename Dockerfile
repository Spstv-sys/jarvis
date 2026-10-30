FROM node:22-slim AS frontend
WORKDIR /ui
COPY ui/web/package.json ui/web/package-lock.json ./
RUN npm ci
COPY ui/web/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TESSERACT_PATH=/usr/bin/tesseract \
    JARVIS_HEADLESS=1 \
    JARVIS_HOST=0.0.0.0 \
    JARVIS_DATA_DIR=/app/data \
    HF_HOME=/app/data/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tesseract-ocr \
        tesseract-ocr-ukr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir .
COPY --from=frontend /ui/dist ./ui/web/dist

EXPOSE 8001

CMD ["python", "server.py"]
