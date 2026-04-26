"""
Utility functions for CNINFO Financial Analyzer
"""

import os
import re
import hashlib
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from loguru import logger


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Load configuration from YAML file

    Args:
        config_path: Path to configuration file

    Returns:
        Configuration dictionary
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def setup_directories(config: Dict[str, Any]) -> None:
    """
    Create necessary directories for the project

    Args:
        config: Configuration dictionary
    """
    dirs = [
        config['downloader']['download_path'],
        config['parser']['output_path'],
        config['output']['results_path'],
        config.get('output', {}).get(
            'intermediate_path',
            os.path.join(config['output']['results_path'], 'intermediate')
        ),
        'logs',
        'data/dictionaries'
    ]

    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    logger.info(f"Created {len(dirs)} directories")


def setup_logging(config: Dict[str, Any]) -> None:
    """
    Setup logging configuration

    Args:
        config: Configuration dictionary
    """
    log_config = config.get('logging', {})
    log_file = log_config.get('log_file', 'logs/analyzer.log')
    log_level = log_config.get('level', 'INFO')

    # Create logs directory
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # Configure logger
    logger.add(
        log_file,
        rotation=log_config.get('rotation', '10 MB'),
        level=log_level,
        format=log_config.get('format', '{time} | {level} | {message}')
    )


def normalize_company_code(code: str) -> str:
    """
    Normalize company stock code to 6-digit format

    Args:
        code: Stock code (may have varying formats)

    Returns:
        Normalized 6-digit code
    """
    # Remove non-digits
    code = re.sub(r'\D', '', str(code))

    # Pad with zeros if necessary
    if len(code) < 6:
        code = code.zfill(6)

    return code[:6]


def detect_exchange(stock_code: str) -> str:
    """
    Detect exchange based on stock code prefix.

    Args:
        stock_code: Stock code (any format)

    Returns:
        'szse' for Shenzhen, 'sse' for Shanghai
    """
    code = normalize_company_code(stock_code)
    if code.startswith(('6', '9')):
        return 'sse'
    # 0, 3, 2 开头 → 深交所
    return 'szse'


def calculate_file_hash(file_path: str, algorithm: str = 'md5') -> str:
    """
    Calculate hash of a file

    Args:
        file_path: Path to file
        algorithm: Hash algorithm (md5, sha256)

    Returns:
        Hex digest of file hash
    """
    hash_func = hashlib.new(algorithm)

    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hash_func.update(chunk)

    return hash_func.hexdigest()


def sanitize_filename(filename: str) -> str:
    """
    Remove invalid characters from filename

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Remove invalid characters for filesystem
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')

    # Limit length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:190] + ext

    return filename


def load_word_list(file_path: str) -> List[str]:
    """
    Load word list from file (one word per line)

    Args:
        file_path: Path to word list file

    Returns:
        List of words
    """
    if not os.path.exists(file_path):
        logger.warning(f"Word list file not found: {file_path}")
        return []

    with open(file_path, 'r', encoding='utf-8') as f:
        words = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    return words


def load_sentiment_dict(file_path: str,
                        positive_prefix: str = 'POS:',
                        negative_prefix: str = 'NEG:') -> Dict[str, List[str]]:
    """
    Load sentiment dictionary from file

    File format:
        POS:增长
        POS:盈利
        NEG:下降
        NEG:亏损

    Args:
        file_path: Path to sentiment dictionary
        positive_prefix: Prefix for positive words
        negative_prefix: Prefix for negative words

    Returns:
        Dictionary with 'positive' and 'negative' word lists
    """
    sentiment_dict = {
        'positive': [],
        'negative': []
    }

    if not os.path.exists(file_path):
        logger.error(f"Sentiment dictionary not found: {file_path}")
        return sentiment_dict

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.startswith(positive_prefix):
                word = line[len(positive_prefix):].strip()
                sentiment_dict['positive'].append(word)
            elif line.startswith(negative_prefix):
                word = line[len(negative_prefix):].strip()
                sentiment_dict['negative'].append(word)
            else:
                # Default to positive if no prefix
                sentiment_dict['positive'].append(line)

    logger.info(f"Loaded {len(sentiment_dict['positive'])} positive words, "
                f"{len(sentiment_dict['negative'])} negative words")

    return sentiment_dict


def parse_year_range(year_str: str) -> List[int]:
    """
    Parse year range string to list of years

    Args:
        year_str: Year string (e.g., "2020", "2020-2022", "2020,2021,2022")

    Returns:
        List of years
    """
    years = []

    # Handle comma-separated
    if ',' in year_str:
        years = [int(y.strip()) for y in year_str.split(',')]
    # Handle range
    elif '-' in year_str:
        start, end = map(int, year_str.split('-'))
        years = list(range(start, end + 1))
    # Single year
    else:
        years = [int(year_str)]

    return years


def format_bytes(size: int) -> str:
    """
    Format bytes to human-readable string

    Args:
        size: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def chinese_sentence_split(text: str) -> List[str]:
    """
    Split Chinese text into sentences

    Args:
        text: Input text

    Returns:
        List of sentences
    """
    # Chinese sentence delimiters
    delimiters = r'[。！？；…\n]+'
    sentences = re.split(delimiters, text)

    # Remove empty sentences
    sentences = [s.strip() for s in sentences if s.strip()]

    return sentences


def count_chinese_chars(text: str) -> int:
    """
    Count Chinese characters in text

    Args:
        text: Input text

    Returns:
        Number of Chinese characters
    """
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
    return len(chinese_pattern.findall(text))


def is_valid_report(file_path: str, min_size_kb: int = 100) -> bool:
    """
    Check if downloaded report file is valid

    Args:
        file_path: Path to PDF file
        min_size_kb: Minimum file size in KB

    Returns:
        True if valid, False otherwise
    """
    if not os.path.exists(file_path):
        return False

    # Check file size
    size_kb = os.path.getsize(file_path) / 1024
    if size_kb < min_size_kb:
        logger.warning(f"File too small ({size_kb:.1f} KB): {file_path}")
        return False

    # Check file extension
    if not file_path.lower().endswith('.pdf'):
        logger.warning(f"Not a PDF file: {file_path}")
        return False

    return True


def create_timestamp() -> str:
    """
    Create timestamp string for file naming

    Returns:
        Timestamp string (YYYYMMDD_HHMMSS)
    """
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def get_report_type_chinese(report_type: str, config: Dict[str, Any]) -> str:
    """
    Get Chinese name for report type

    Args:
        report_type: Report type key (e.g., 'annual')
        config: Configuration dictionary

    Returns:
        Chinese report type name
    """
    report_types = config.get('downloader', {}).get('report_types', {})
    return report_types.get(report_type, report_type)
