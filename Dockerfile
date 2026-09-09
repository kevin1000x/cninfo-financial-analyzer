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
#   - gcc/g++ + zlib/jpeg/png dev headers: build wheels that ship no
#     arm64/manylinux binary for this base image
#
# The table engines and OCR fallback are opt-in (requirements-full.txt), so
# their system packages are not installed here. To serve scanned PDFs or
# camelot/tabula extraction, add them back alongside that requirements file:
#   tesseract-ocr tesseract-ocr-chi-sim   for pytesseract
#   poppler-utils                         for pdf2image
#   ghostscript libgl1 libglib2.0-0       for camelot-py[cv]
#   default-jre-headless                  for tabula-py
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
        zlib1g-dev libjpeg-dev libpng-dev \
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

# Second service sharing this container (see api/audit_mount.py). In this
# repository `finaudit/` holds only a README; the deploy snapshot pushed to the
# Space replaces it with the real payload. Either way this COPY succeeds and the
# app starts -- the routes are registered only if the packages actually import.
#
# No pip install: finaudit's sole third-party import is PyYAML, already above.
#
# The directory shape inside finaudit/ is load-bearing, not cosmetic: that
# service derives its data root from its own module path (src/service/api.py
# -> three parents up), so the payload must keep its src/ level with
# metrics/ and data/ as siblings of it. PYTHONPATH points at src/, not here.
COPY finaudit/ /app/finaudit/

# Writable runtime dirs (ephemeral on HF Spaces — restart wipes them, by design)
RUN mkdir -p data/raw data/parsed data/results data/_web_jobs data/parsed_text logs

# HF Spaces / Docker runs as uid 1000 by default; align ownership
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/finaudit/src \
    PORT=7860

EXPOSE 7860

# Web server: uvicorn binds 0.0.0.0 (container network, NOT 127.0.0.1 like local)
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
