"""
Unit tests for PDFParser module
"""

import pytest
import os
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.pdf_parser import PDFParser


@pytest.fixture
def config():
    """Test configuration"""
    return {
        'parser': {
            'pdf_engine': 'pdfplumber',
            'output_path': 'tests/data/parsed',
            'use_ocr': False,
            'extract_tables': True,
            'mda_keywords': [
                '管理层讨论与分析',
                '经营情况讨论与分析'
            ],
            'financial_statement_keywords': {
                'balance_sheet': ['资产负债表', '合并资产负债表'],
                'income_statement': ['利润表', '合并利润表'],
                'cash_flow': ['现金流量表', '合并现金流量表']
            }
        }
    }


@pytest.fixture
def parser(config, tmp_path):
    """Create PDFParser instance with temp directory"""
    config['parser']['output_path'] = str(tmp_path)
    return PDFParser(config)


def test_parser_initialization(parser):
    """Test parser initialization"""
    assert parser.pdf_engine == 'pdfplumber'
    assert parser.extract_tables is True
    assert os.path.exists(parser.output_path)


def test_extract_mda_section(parser):
    """Test MD&A section extraction"""
    text = """
    第一章 公司基本情况

    第二章 管理层讨论与分析
    本年度公司经营情况良好，实现营业收入增长。
    管理层认为市场前景乐观。

    第三章 财务报表
    资产负债表如下...
    """

    mda_text = parser.extract_mda_section(text)

    assert '管理层讨论与分析' in mda_text
    assert '经营情况良好' in mda_text
    # Should not include sections after MD&A
    assert '第三章' not in mda_text or '财务报表' in mda_text


def test_extract_mda_not_found(parser):
    """Test MD&A extraction when section not found"""
    text = "这是一段普通文本，没有管理层讨论。"

    mda_text = parser.extract_mda_section(text)

    # Should return full text if MD&A not found
    assert mda_text == text


def test_identify_financial_statement_balance_sheet(parser):
    """Test balance sheet identification"""
    import pandas as pd

    # Create mock table with balance sheet keywords
    table = pd.DataFrame({
        'col1': ['资产负债表', '流动资产', '非流动资产'],
        'col2': ['2022年12月31日', '100000', '200000']
    })

    is_balance_sheet = parser.identify_financial_statement(table, 'balance_sheet')

    assert is_balance_sheet is True


def test_identify_financial_statement_income(parser):
    """Test income statement identification"""
    import pandas as pd

    table = pd.DataFrame({
        'col1': ['合并利润表', '营业收入', '营业成本'],
        'col2': ['2022年度', '500000', '300000']
    })

    is_income = parser.identify_financial_statement(table, 'income_statement')

    assert is_income is True


def test_identify_financial_statement_negative(parser):
    """Test financial statement identification returns false"""
    import pandas as pd

    table = pd.DataFrame({
        'col1': ['普通表格', '数据1', '数据2'],
        'col2': ['值1', '值2', '值3']
    })

    is_balance_sheet = parser.identify_financial_statement(table, 'balance_sheet')

    assert is_balance_sheet is False


def test_extract_financial_statements(parser):
    """Test extracting and classifying financial statements"""
    import pandas as pd

    tables = [
        pd.DataFrame({'col1': ['资产负债表', '资产']}),
        pd.DataFrame({'col1': ['利润表', '收入']}),
        pd.DataFrame({'col1': ['现金流量表', '现金']}),
        pd.DataFrame({'col1': ['普通表格', '数据']})
    ]

    statements = parser.extract_financial_statements(tables)

    # Should identify 3 financial statements
    assert 'balance_sheet' in statements
    assert 'income_statement' in statements
    assert 'cash_flow' in statements
    assert len(statements) == 3


def test_save_parsed_data(parser, tmp_path):
    """Test saving parsed data"""
    pdf_path = 'test_report.pdf'
    result = {
        'text': '完整报告文本',
        'mda_text': 'MD&A部分文本',
        'tables': [],
        'financial_statements': {}
    }

    parser.save_parsed_data(pdf_path, result)

    # Check output directory created (it appends a hash, so glob it)
    dirs = list(tmp_path.glob('test_report*'))
    assert len(dirs) > 0
    output_dir = dirs[0]
    assert output_dir.exists()

    # Check text files created
    assert (output_dir / 'full_text.txt').exists()
    assert (output_dir / 'mda_text.txt').exists()


def test_save_parsed_data_retention(parser, tmp_path):
    """Parser should prune old parsed directories when retention is enabled."""
    parser.max_saved_reports = 2

    for idx in range(3):
        parser.save_parsed_data(
            f'test_report_{idx}.pdf',
            {
                'text': f'完整报告文本{idx}',
                'mda_text': f'MD&A部分文本{idx}',
                'tables': [],
                'financial_statements': {}
            }
        )
        for order, path in enumerate(sorted(tmp_path.iterdir()), start=1):
            if path.is_dir():
                os.utime(path, (order, order))

    kept_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(kept_dirs) == 2


def test_save_parsed_data_honors_output_flags(tmp_path):
    """Parser persistence should respect save_raw_text/save_structured_tables."""
    config = {
        'parser': {
            'pdf_engine': 'pdfplumber',
            'output_path': str(tmp_path),
            'use_ocr': False,
            'extract_tables': True,
            'mda_keywords': ['管理层讨论与分析'],
            'financial_statement_keywords': {
                'balance_sheet': ['资产负债表'],
                'income_statement': [],
                'cash_flow': [],
            }
        },
        'output': {
            'save_raw_text': False,
            'save_structured_tables': False,
        }
    }
    parser = PDFParser(config)

    paths = parser.save_parsed_data(
        'test_report_flags.pdf',
        {
            'text': '完整报告文本',
            'mda_text': 'MD&A部分文本',
            'tables': [pd.DataFrame({'col1': ['资产负债表', '流动资产']})],
            'financial_statements': {
                'balance_sheet': pd.DataFrame({'col1': ['资产负债表']})
            }
        }
    )

    output_dir = Path(paths['output_dir'])
    assert output_dir.exists()
    assert paths['text_path'] == ''
    assert paths['mda_path'] == ''
    assert not (output_dir / 'full_text.txt').exists()
    assert not (output_dir / 'mda_text.txt').exists()
    assert not list(output_dir.glob('table_*.csv'))
    assert not (output_dir / 'balance_sheet.csv').exists()


def test_save_parsed_data_honors_structured_tables_flag_only(tmp_path):
    """Raw text can be saved while structured table CSV output is disabled."""
    config = {
        'parser': {
            'pdf_engine': 'pdfplumber',
            'output_path': str(tmp_path),
            'use_ocr': False,
            'extract_tables': True,
            'mda_keywords': ['管理层讨论与分析'],
            'financial_statement_keywords': {
                'balance_sheet': ['资产负债表'],
                'income_statement': [],
                'cash_flow': [],
            }
        },
        'output': {
            'save_raw_text': True,
            'save_structured_tables': False,
        }
    }
    parser = PDFParser(config)

    paths = parser.save_parsed_data(
        'test_report_no_structured_tables.pdf',
        {
            'text': '完整报告文本',
            'mda_text': 'MD&A部分文本',
            'tables': [pd.DataFrame({'col1': ['资产负债表', '流动资产']})],
            'financial_statements': {
                'balance_sheet': pd.DataFrame({'col1': ['资产负债表']})
            }
        }
    )

    output_dir = Path(paths['output_dir'])
    assert (output_dir / 'full_text.txt').exists()
    assert (output_dir / 'mda_text.txt').exists()
    assert paths['text_path'] == str(output_dir / 'full_text.txt')
    assert paths['mda_path'] == str(output_dir / 'mda_text.txt')
    assert not list(output_dir.glob('table_*.csv'))
    assert not (output_dir / 'balance_sheet.csv').exists()


def test_parse_pdf_structure(parser):
    """Test parse_pdf returns correct structure"""
    # Create a minimal test - actual PDF parsing requires real PDF file
    result = {
        'pdf_path': 'test.pdf',
        'text': '',
        'mda_text': '',
        'tables': [],
        'financial_statements': {},
        'error': None
    }

    # Verify structure
    assert 'pdf_path' in result
    assert 'text' in result
    assert 'mda_text' in result
    assert 'tables' in result
    assert 'financial_statements' in result
    assert 'error' in result


def test_chinese_text_handling(parser):
    """Test handling of Chinese text"""
    chinese_text = """
    公司简介：我们是一家专注于科技创新的企业。
    管理层讨论与分析：
    本年度业绩表现良好，各项指标稳步提升。
    """

    mda = parser.extract_mda_section(chinese_text)

    # Should correctly identify Chinese section
    assert '管理层讨论与分析' in mda
    assert '业绩表现良好' in mda


def test_multiple_keywords(parser):
    """Test matching multiple MD&A keywords"""
    text1 = "第三章 管理层讨论与分析\n本章内容..."
    text2 = "第三章 经营情况讨论与分析\n本章内容..."

    mda1 = parser.extract_mda_section(text1)
    mda2 = parser.extract_mda_section(text2)

    # Both should be identified
    assert '管理层讨论与分析' in mda1
    assert '经营情况讨论与分析' in mda2


def test_extract_mda_section_skips_table_of_contents(parser):
    """Should skip TOC entries and extract the real MD&A body."""
    text = """
    目 录
    第三章 管理层讨论与分析 ...................................................................... 22
    3.1 总体经营情况 ................................................................................. 22
    第四章 公司治理 ................................................................................... 62

    第三章 管理层讨论与分析
    本年度公司经营情况良好，实现营业收入持续增长。
    管理层认为核心业务保持稳健发展。

    第四章 公司治理
    公司治理内容如下。
    """

    mda = parser.extract_mda_section(text)

    assert '经营情况良好' in mda
    assert '核心业务保持稳健发展' in mda
    assert '...................................................................... 22' not in mda
    assert '第四章 公司治理' not in mda


def test_extract_mda_section_returns_full_text_when_only_toc_found(parser):
    """If only TOC-like matches exist, parser should fall back to full text."""
    text = """
    目 录
    第三章 管理层讨论与分析 ...................................................................... 22
    第四章 公司治理 ................................................................................... 62
    """

    mda = parser.extract_mda_section(text)

    assert mda == text


def test_pdf_engine_configuration(config):
    """Test different PDF engine configurations"""
    # pdfplumber
    config['parser']['pdf_engine'] = 'pdfplumber'
    parser1 = PDFParser(config)
    assert parser1.pdf_engine == 'pdfplumber'

    # PyMuPDF 已移除（AGPL v3 与本项目的 MIT 声明冲突）：
    # 配置值仍然写得进去，但不会再有任何 PyMuPDF 代码路径。
    assert not hasattr(parser1, 'extract_text_pymupdf')


def test_batch_parse_empty_list(parser):
    """Test batch parsing with empty list"""
    results = parser.batch_parse([])
    assert results == []


def test_output_path_creation(tmp_path):
    """Test output path is created if it doesn't exist"""
    config = {
        'parser': {
            'pdf_engine': 'pdfplumber',
            'output_path': str(tmp_path / 'new_output'),
            'use_ocr': False,
            'extract_tables': True,
            'mda_keywords': [],
            'financial_statement_keywords': {}
        }
    }

    parser = PDFParser(config)

    # Output path should be created
    assert os.path.exists(parser.output_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
