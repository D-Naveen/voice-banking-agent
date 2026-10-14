FROM python:3.12-slim

# Build with  --build-arg INSTALL_SPOOF=1  to include the local Hugging Face anti-spoof model runtime.
ARG INSTALL_SPOOF=0
WORKDIR /app

COPY requirements.txt requirements-spoof.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$INSTALL_SPOOF" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir -r requirements-spoof.txt; \
    fi

COPY app ./app
COPY frontend ./frontend
RUN useradd -m appuser && mkdir -p /data && chown appuser /data
USER appuser

ENV DATABASE_URL=sqlite:////data/voice_banking.db
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
