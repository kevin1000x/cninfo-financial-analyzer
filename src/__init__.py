"""
CNINFO Financial Analyzer

A comprehensive toolkit for downloading, parsing, and analyzing
Chinese financial reports from CNINFO (巨潮资讯网).

Main Components:
- CNINFODownloader: Download reports from cninfo.com.cn
- PDFParser: Extract text and tables from PDFs
- TextAnalyzer: Calculate Tone and Fog Index
- MetricsCalculator: Calculate TNI and performance metrics
- FinancialAnalysisPipeline: End-to-end orchestration

Example:
    >>> from src.pipeline import FinancialAnalysisPipeline
    >>> pipeline = FinancialAnalysisPipeline()
    >>> results = pipeline.run(
    ...     company_codes=['000001', '600000'],
    ...     years=[2020, 2021, 2022]
    ... )

License: MIT
Author: CNINFO Financial Analyzer Maintainers
Version: 1.0.0
"""

__version__ = '1.0.0'
__author__ = 'CNINFO Financial Analyzer Maintainers'
__email__ = ''
__license__ = 'MIT'

from .downloader import CNINFODownloader, SeleniumDownloader, PlaywrightDownloader
from .pdf_parser import PDFParser
from .text_analyzer import TextAnalyzer
from .metrics import MetricsCalculator, load_financial_data_from_csv
from .pipeline import FinancialAnalysisPipeline
from .utils import (
    load_config,
    normalize_company_code,
    detect_exchange,
    load_sentiment_dict,
    load_word_list
)

__all__ = [
    'CNINFODownloader',
    'SeleniumDownloader',
    'PlaywrightDownloader',
    'PDFParser',
    'TextAnalyzer',
    'MetricsCalculator',
    'FinancialAnalysisPipeline',
    'load_config',
    'normalize_company_code',
    'detect_exchange',
    'load_sentiment_dict',
    'load_word_list',
    'load_financial_data_from_csv',
]
