"""
Setup script for CNINFO Financial Analyzer
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / 'README.md'
long_description = readme_file.read_text(encoding='utf-8') if readme_file.exists() else ''

setup(
    name='cninfo-financial-analyzer',
    version='1.0.0',
    author='CNINFO Financial Analyzer Maintainers',
    author_email='',
    description='A comprehensive toolkit for analyzing Chinese financial reports from CNINFO',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/kevin1000x/cninfo-financial-analyzer',
    packages=find_packages(),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'Intended Audience :: Financial and Insurance Industry',
        'Topic :: Office/Business :: Financial',
        'Topic :: Scientific/Engineering :: Information Analysis',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Natural Language :: Chinese (Simplified)',
    ],
    python_requires='>=3.8',
    install_requires=[
        'aiohttp>=3.9.0',
        'requests>=2.31.0',
        'httpx>=0.25.0',
        'pdfplumber>=0.10.0',
        'PyMuPDF>=1.23.0',
        'jieba>=0.42.1',
        'pandas>=2.1.0',
        'numpy>=1.24.0',
        'openpyxl>=3.1.0',
        'pyarrow>=14.0.0',
        'PyYAML>=6.0',
        'tqdm>=4.66.0',
        'loguru>=0.7.0',
        'python-dateutil>=2.8.0',
        'beautifulsoup4>=4.12.0',
        'lxml>=4.9.0',
        'aiofiles>=23.2.0',
        'regex>=2023.10.0',
        'click>=8.1.0',
        'rich>=13.6.0',
    ],
    extras_require={
        'full': [
            'tabula-py>=2.8.0',
            'camelot-py[cv]>=0.11.0',
            'pytesseract>=0.3.10',
            'pdf2image>=1.16.0',
            'pypinyin>=0.49.0',
            'opencc-python-reimplemented>=0.1.7',
            'fastparquet>=2023.10.0',
            'matplotlib>=3.8.0',
            'seaborn>=0.13.0',
        ],
        'automation': [
            'selenium>=4.15.0',
            'playwright>=1.40.0',
        ],
        'dev': [
            'pytest>=7.4.0',
            'pytest-cov>=4.1.0',
            'pytest-asyncio>=0.21.0',
            'pytest-mock>=3.12.0',
            'black>=23.10.0',
            'flake8>=6.1.0',
            'mypy>=1.6.0',
            'pre-commit>=3.5.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'cninfo-analyzer=src.pipeline:main',
        ],
    },
    include_package_data=True,
    package_data={
        'src': ['*.yaml'],
        '': ['*.txt', '*.md'],
    },
    keywords='finance nlp chinese text-analysis sentiment-analysis financial-reports cninfo',
    project_urls={
        'Bug Reports': 'https://github.com/kevin1000x/cninfo-financial-analyzer/issues',
        'Source': 'https://github.com/kevin1000x/cninfo-financial-analyzer',
        'Documentation': 'https://github.com/kevin1000x/cninfo-financial-analyzer/wiki',
    },
    license='MIT',
)
