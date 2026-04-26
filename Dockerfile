# CNINFO Financial Analyzer - Docker Image
# Usage: docker build -t cninfo-analyzer .
#        docker run -v $(pwd)/data:/app/data cninfo-analyzer

FROM python:3.10-slim

LABEL maintainer="your.email@example.com"
LABEL description="CNINFO Financial Report Analyzer"
LABEL version="1.0.0"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libjpeg-dev \
    libpng-dev \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    ghostscript \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install package in editable mode
RUN pip install -e .

# Create necessary directories
RUN mkdir -p data/raw data/parsed data/results data/dictionaries logs

# Create example sentiment dictionary if not exists
RUN if [ ! -f data/dictionaries/cn_financial_sentiment.txt ]; then \
        cp examples/cn_financial_sentiment.txt data/dictionaries/; \
    fi

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Create volume mount points
VOLUME ["/app/data", "/app/logs"]

# Default command
CMD ["python", "-m", "src.pipeline", "analyze", \
     "--companies", "examples/company_list.csv", \
     "--years", "2020-2021", \
     "--financial-data", "examples/financial_data.csv"]

# Alternative: Run as interactive shell
# CMD ["/bin/bash"]