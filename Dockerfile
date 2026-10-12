FROM python:3.12-slim

# OCR em português
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -r -m -d /app juris
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docs ./docs
COPY README.md config.example.env ./

ENV JURIS_DATA_DIR=/data \
    JURIS_HOST=0.0.0.0 \
    JURIS_PORT=8000 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown juris /data
USER juris
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --start-period=20s --interval=60s --timeout=10s CMD python -c "import urllib.request,os;urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+os.environ.get('JURIS_PORT','8000')+'/api/status',headers={'X-App-Token':os.environ.get('JURIS_APP_TOKEN','')}))" || exit 1
# Um único worker: o agendador de coletas roda dentro do processo.
CMD ["sh", "-c", "uvicorn app.api:app --host $JURIS_HOST --port $JURIS_PORT --workers 1 --proxy-headers"]
