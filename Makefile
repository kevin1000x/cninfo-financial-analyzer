# Makefile for CNINFO Financial Analyzer

.PHONY: help install install-dev install-full test test-cov clean lint format run-example setup-dirs setup-dict typecheck download-example parse-example analyze-example check

help:
	@echo "CNINFO Financial Analyzer - Available Commands"
	@echo ""
	@echo "  make install          Install package and dependencies"
	@echo "  make install-dev      Install with development dependencies"
	@echo "  make test             Run test suite"
	@echo "  make test-cov         Run tests with coverage report"
	@echo "  make lint             Run code linting"
	@echo "  make typecheck        Run mypy with repo config"
	@echo "  make format           Format code with black"
	@echo "  make clean            Clean build artifacts"
	@echo "  make setup-dirs       Create necessary directories"
	@echo "  make setup-dict       Create example sentiment dictionary"
	@echo "  make run-example      Run complete analysis example"
	@echo ""

install:
	pip install -e .

install-dev:
	pip install -e ".[dev,full]"

install-full:
	pip install -e ".[full]"

test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=src --cov-report=html --cov-report=term

lint:
	flake8 src/ tests/
	mypy --config-file mypy.ini src/

typecheck:
	mypy --config-file mypy.ini src/

format:
	black src/ tests/ examples/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

setup-dirs:
	mkdir -p data/raw
	mkdir -p data/parsed
	mkdir -p data/results
	mkdir -p data/dictionaries
	mkdir -p logs
	mkdir -p tests/data
	touch data/raw/.gitkeep
	touch data/parsed/.gitkeep
	touch data/results/.gitkeep
	touch logs/.gitkeep
	@echo "✓ Directories created"

setup-dict:
	@if [ ! -f data/dictionaries/cn_financial_sentiment.txt ]; then \
		cp examples/cn_financial_sentiment.txt data/dictionaries/; \
		echo "✓ Sentiment dictionary copied to data/dictionaries/"; \
	else \
		echo "✓ Sentiment dictionary already exists"; \
	fi

run-example:
	python examples/full_analysis_example.py

download-example:
	python -m src.pipeline download \
		--companies examples/company_list.csv \
		--years 2020-2021 \
		--types annual

parse-example:
	python -m src.pipeline parse \
		--input data/raw \
		--output data/parsed

analyze-example:
	python -m src.pipeline analyze \
		--companies examples/company_list.csv \
		--years 2020-2021 \
		--financial-data examples/financial_data.csv

# Development helpers
check:
	@echo "Running all checks..."
	make lint
	make test

init: setup-dirs setup-dict install
	@echo ""
	@echo "✓ Project initialized!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Review config.yaml"
	@echo "  2. Run: make run-example"
	@echo ""
