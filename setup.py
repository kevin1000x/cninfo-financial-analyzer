"""
Setup script for CNINFO Financial Analyzer

Dependency lists are parsed out of the requirements-*.txt files rather than
restated here; the two copies used to drift apart.
"""

from setuptools import setup, find_packages
from pathlib import Path

ROOT = Path(__file__).parent

# Read README for long description
readme_file = ROOT / 'README.md'
long_description = readme_file.read_text(encoding='utf-8') if readme_file.exists() else ''


def read_requirements(filename: str) -> list:
    """Parse a requirements file into a list of requirement strings.

    Comments (including the trailing `# apt: ...` notes that record which system
    binary each optional wheel needs) and `-r` includes are dropped: extras are
    additive to install_requires, so a `-r requirements.txt` line in the dev file
    must not restate the runtime set.
    """
    lines = (ROOT / filename).read_text(encoding='utf-8').splitlines()
    reqs = []
    for raw in lines:
        line = raw.split('#')[0].strip()
        if line and not line.startswith('-'):
            reqs.append(line)
    return reqs


setup(
    name='cninfo-financial-analyzer',
    version='1.0.0',
    author='CNINFO Financial Analyzer Maintainers',
    author_email='',
    description='A comprehensive toolkit for analyzing Chinese financial reports from CNINFO',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/kevin1000x/cninfo-financial-analyzer',
    # tests/ has an __init__.py, so a bare find_packages() shipped the test suite
    # as an importable top-level `tests` package.
    packages=find_packages(include=['src', 'src.*', 'api', 'api.*']),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'Intended Audience :: Financial and Insurance Industry',
        'Topic :: Office/Business :: Financial',
        'Topic :: Scientific/Engineering :: Information Analysis',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Natural Language :: Chinese (Simplified)',
    ],
    # 3.11, not the 3.8 this used to claim: pandas>=3 and numpy>=2.4 — both hard
    # runtime dependencies — declare Requires-Python >=3.11, mypy.ini type-checks
    # against 3.11 and the published image is python:3.12-slim. Nothing tested
    # 3.8 through 3.10.
    python_requires='>=3.11',
    install_requires=read_requirements('requirements.txt'),
    extras_require={
        'full': read_requirements('requirements-full.txt'),
        'automation': read_requirements('requirements-automation.txt'),
        'dev': read_requirements('requirements-dev.txt'),
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
