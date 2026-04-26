"""
Unit tests for TextAnalyzer module
"""

import pytest
import os
import tempfile
from pathlib import Path

# Import module to test
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.text_analyzer import TextAnalyzer, ChineseCommonVocabGenerator
from src.utils import load_config


@pytest.fixture
def config():
    """Load test configuration"""
    return {
        'analyzer': {
            'segmentation_tool': 'jieba',
            'min_word_length': 2,
            'remove_stopwords': False,
            'complex_word_threshold': 2,
            'positive_prefix': 'POS:',
            'negative_prefix': 'NEG:'
        }
    }


@pytest.fixture
def sentiment_dict_file():
    """Create temporary sentiment dictionary"""
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.txt') as f:
        f.write('POS:增长\n')
        f.write('POS:盈利\n')
        f.write('POS:优化\n')
        f.write('POS:提升\n')
        f.write('NEG:下降\n')
        f.write('NEG:亏损\n')
        f.write('NEG:风险\n')
        f.write('NEG:减少\n')
        temp_path = f.name

    yield temp_path

    # Cleanup
    os.unlink(temp_path)


@pytest.fixture
def analyzer(config, sentiment_dict_file):
    """Create TextAnalyzer instance"""
    return TextAnalyzer(config, sentiment_dict_file)


def test_load_sentiment_dict(analyzer):
    """Test sentiment dictionary loading"""
    assert len(analyzer.sentiment_dict['positive']) == 4
    assert len(analyzer.sentiment_dict['negative']) == 4
    assert '增长' in analyzer.sentiment_dict['positive']
    assert '亏损' in analyzer.sentiment_dict['negative']


def test_sentiment_dict_uses_sets(analyzer):
    """Sentiment dictionaries should use sets for fast lookup."""
    assert isinstance(analyzer.sentiment_dict['positive'], set)
    assert isinstance(analyzer.sentiment_dict['negative'], set)


def test_segment_text(analyzer):
    """Test Chinese text segmentation"""
    text = "公司业绩实现快速增长，营业收入大幅提升。"
    words = analyzer.segment_text(text)

    assert isinstance(words, list)
    assert len(words) > 0
    # Jieba might segment "公司业绩" as one word, so just check if parts are in it
    assert any('公司' in w for w in words)
    assert any('增长' in w for w in words)


def test_count_sentiment_words(analyzer):
    """Test sentiment word counting"""
    words = ['增长', '盈利', '优化', '下降', '风险', '中性词']
    pos_count, neg_count = analyzer.count_sentiment_words(words)

    assert pos_count == 3  # 增长, 盈利, 优化
    assert neg_count == 2  # 下降, 风险


def test_calculate_tone_positive(analyzer):
    """Test Tone calculation with positive text"""
    text = "公司业绩实现快速增长，盈利能力持续优化提升。"
    result = analyzer.calculate_tone(text)

    assert 'tone_raw' in result
    assert 'pos_word_count' in result
    assert 'neg_word_count' in result

    assert result['pos_word_count'] > 0
    assert result['tone_raw'] > 0  # Should be positive


def test_calculate_tone_negative(analyzer):
    """Test Tone calculation with negative text"""
    text = "公司业绩出现下降，面临严重亏损风险，盈利能力减少。"
    result = analyzer.calculate_tone(text)

    assert result['neg_word_count'] > 0
    assert result['tone_raw'] < 0  # Should be negative


def test_calculate_tone_neutral(analyzer):
    """Test Tone calculation with neutral text"""
    text = "公司正常运营，日常业务进行中。"
    result = analyzer.calculate_tone(text)

    # No sentiment words, tone should be 0
    assert result['tone_raw'] == 0.0


def test_split_sentences(analyzer):
    """Test sentence splitting"""
    text = "这是第一句话。这是第二句话！这是第三句话？"
    sentences = analyzer.split_sentences(text)

    assert len(sentences) == 3
    assert sentences[0] == "这是第一句话"


def test_is_complex_word(analyzer):
    """Test complex word identification"""
    # Short words (not complex)
    assert not analyzer.is_complex_word("的")
    assert not analyzer.is_complex_word("在")

    # Long words (complex > 3 chars if no common_vocab is available)
    assert analyzer.is_complex_word("资产负债表")
    assert analyzer.is_complex_word("营业收入")


def test_calculate_fog_index(analyzer):
    """Test Fog Index calculation"""
    text = """
    公司管理层认为，本年度经营情况良好。
    营业收入实现大幅增长。
    净利润持续提升。
    资产负债表保持健康状态。
    """

    result = analyzer.calculate_fog_index(text)

    assert 'fog_index' in result
    assert 'avg_sentence_length' in result
    assert 'complex_word_pct' in result
    assert 'sentence_count' in result

    assert result['fog_index'] > 0
    assert result['sentence_count'] == 4


def test_calculate_alternative_metrics(analyzer):
    """Test alternative readability metrics"""
    text = "公司业绩良好，营业收入增长。"

    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as f:
        f.write(text)
        temp_path = f.name

    result = analyzer.calculate_alternative_metrics(text, temp_path)

    assert 'char_count' in result
    assert 'chinese_char_count' in result
    assert 'file_size_bytes' in result

    assert result['char_count'] > 0
    assert result['file_size_bytes'] > 0

    os.unlink(temp_path)


def test_analyze_text_comprehensive(analyzer):
    """Test comprehensive text analysis"""
    text = """
    公司本年度经营情况良好，实现营业收入大幅增长，盈利能力持续优化提升。
    管理层认为，公司核心竞争力不断增强。
    然而，市场仍存在一定风险，需要持续关注。
    """

    result = analyzer.analyze_text(text)

    # Check all key metrics are present
    assert result['success'] is True
    assert 'tone_raw' in result
    assert 'fog_index' in result
    assert 'char_count' in result

    # Tone should be positive (more positive words)
    assert result['tone_raw'] > 0

    # Should have identified some complex words
    assert result['complex_word_count'] > 0


def test_analyze_empty_text(analyzer):
    """Test analysis with empty text"""
    result = analyzer.analyze_text("")

    assert result['tone_raw'] == 0.0
    assert result['fog_index'] == 0.0


def test_analyze_text_with_file_path(analyzer):
    """Test analysis with file path for size metric"""
    text = "公司业绩增长，盈利提升。"

    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as f:
        f.write(text * 100)  # Make it larger
        temp_path = f.name

    result = analyzer.analyze_text(text, temp_path)

    assert result['file_size_bytes'] > 0

    os.unlink(temp_path)


def test_batch_analyze(analyzer):
    """Test batch analysis"""
    texts = [
        ("公司业绩增长，盈利提升。", "file1.txt"),
        ("市场面临风险，业绩下降。", "file2.txt"),
        ("正常运营中。", "file3.txt")
    ]

    results = analyzer.batch_analyze(texts)

    assert len(results) == 3
    assert all('tone_raw' in r for r in results)
    assert results[0]['tone_raw'] > 0  # Positive
    assert results[1]['tone_raw'] < 0  # Negative
    assert results[2]['tone_raw'] == 0  # Neutral


def test_common_vocab_generator():
    """Test common vocabulary generator"""
    # Create temporary corpus files
    corpus_files = []

    for i in range(2):
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as f:
            f.write("公司 业绩 增长 盈利 " * 10)
            corpus_files.append(f.name)

    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.txt') as f:
        output_path = f.name

    # Generate common vocabulary
    vocab = ChineseCommonVocabGenerator.generate_from_corpus(
        corpus_files,
        output_path,
        top_n=10
    )

    assert len(vocab) > 0
    assert os.path.exists(output_path)

    # Cleanup
    for file in corpus_files:
        os.unlink(file)
    os.unlink(output_path)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
