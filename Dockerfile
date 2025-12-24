# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Faster, cleaner Python in containers
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# System deps (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the source code
COPY . /app

# Cloud Run listens on $PORT
EXPOSE 8000

# Use shell form so $PORT env var is expanded
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
