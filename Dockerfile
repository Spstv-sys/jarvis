FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TESSERACT_PATH=/usr/bin/tesseract

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.server.txt .
RUN pip install --no-cache-dir -r requirements.server.txt

COPY . .

EXPOSE 8001

CMD ["python", "server.py"]
