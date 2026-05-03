# CNINFO Financial Analyzer — web API image
# Default usage (HF Spaces / docker run):
#   docker build -t cninfo-analyzer .
#   docker run -p 7860:7860 \
#     -e API_TOKEN=... -e CNINFO_COOKIES_JSON='{"JSESSIONID":"..."}' \
#     cninfo-analyzer
#
# CLI mode (override entrypoint):
#   docker run --entrypoint python cninfo-analyzer \
#     -m src.pipeline analyze --companies examples/company_list.csv \
#     --years 2020-2022 --financial-data examples/financial_data.csv

FROM python:3.12-slim

LABEL description="CNINFO Financial Report Analyzer (FastAPI web layer)"

WORKDIR /app

# System libs:
#   - gcc/g++/lib*-dev: build wheels that don't ship arm64/manylinux binaries
#   - tesseract + chi-sim: OCR fallback for scanned PDFs
#   - ghostscript + poppler: camelot table extraction
#   - libgl1 + libglib2.0-0: opencv (camelot[cv] dep)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev libpng-dev \
        tesseract-ocr tesseract-ocr-chi-sim \
        ghostscript poppler-utils \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Cache layer for Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source — only what runtime needs (tests/docs/scripts excluded via .dockerignore)
COPY src/ ./src/
COPY api/ ./api/
COPY config.yaml ./
COPY examples/ ./examples/
COPY data/dictionaries/ ./data/dictionaries/

# Writable runtime dirs (ephemeral on HF Spaces — restart wipes them, by design)
RUN mkdir -p data/raw data/parsed data/results data/_web_jobs data/parsed_text logs

# HF Spaces / Docker runs as uid 1000 by default; align ownership
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860

EXPOSE 7860

# Web server: uvicorn binds 0.0.0.0 (container network, NOT 127.0.0.1 like local)
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
