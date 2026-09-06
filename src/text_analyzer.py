"""
Text Analyzer Module
Calculates Tone and Readability (Fog Index) for Chinese financial texts

References:
- Loughran & McDonald (2011) sentiment analysis
- Li (2008) for readability metrics
- Chinese Financial Sentiment Dictionary (Jiang Fuwei Team)
"""

import re
import os
from typing import Dict, List, Tuple, Optional, Set
from loguru import logger

# Chinese NLP
import jieba


class TextAnalyzer:
    """
    Analyzer for Chinese financial text
    Computes Tone (sentiment) and Fog Index (readability)
    """

    def __init__(self, config: Dict, sentiment_dict_path: str):
        """
        Initialize text analyzer

        Args:
            config: Configuration dictionary
            sentiment_dict_path: Path to sentiment dictionary
        """
        self.config = config['analyzer']
        self.segmentation_tool = self.config.get('segmentation_tool', 'jieba')
        self.min_word_length = self.config.get('min_word_length', 2)
        self.complex_word_threshold = self.config.get('complex_word_threshold', 2)

        # Load sentiment dictionary
        self.sentiment_dict = self.load_sentiment_dict(sentiment_dict_path)

        # Load stopwords if configured
        self.remove_stopwords = self.config.get('remove_stopwords', True)
        stopwords_path = self.config.get('stopwords_path')
        self.stopwords = set()
        if self.remove_stopwords:
            if stopwords_path and os.path.exists(stopwords_path):
                with open(stopwords_path, 'r', encoding='utf-8') as f:
                    self.stopwords = set(line.strip() for line in f if line.strip())
            elif stopwords_path:
                logger.warning(f"Stopwords file not found: {stopwords_path}")

        # Load common vocabulary for readability
        common_vocab_path = self.config.get('common_vocab_path')
        self.common_vocab = set()
        if common_vocab_path and os.path.exists(common_vocab_path):
            with open(common_vocab_path, 'r', encoding='utf-8') as f:
                self.common_vocab = set(line.strip() for line in f if line.strip())
        else:
            logger.warning("Common vocabulary not loaded, using character count threshold")

        # Initialize jieba
        if self.segmentation_tool == 'jieba':
            # Add custom financial terms to jieba dictionary
            self._add_custom_financial_terms()

        logger.info(f"Text Analyzer initialized: {len(self.sentiment_dict['positive'])} positive, "
                    f"{len(self.sentiment_dict['negative'])} negative words")

    def load_sentiment_dict(self, dict_path: str) -> Dict[str, Set[str]]:
        """
        Load sentiment dictionary

        Format:
            POS:增长
            POS:盈利
            NEG:下降
            NEG:亏损

        Args:
            dict_path: Path to sentiment dictionary file

        Returns:
            Dictionary with 'positive' and 'negative' word lists
        """
        sentiment_dict = {
            'positive': set(),
            'negative': set()
        }

        if not os.path.exists(dict_path):
            logger.error(f"Sentiment dictionary not found: {dict_path}")
            return sentiment_dict

        pos_prefix = self.config.get('positive_prefix', 'POS:')
        neg_prefix = self.config.get('negative_prefix', 'NEG:')

        with open(dict_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if line.startswith(pos_prefix):
                    word = line[len(pos_prefix):].strip()
                    sentiment_dict['positive'].add(word)
                elif line.startswith(neg_prefix):
                    word = line[len(neg_prefix):].strip()
                    sentiment_dict['negative'].add(word)
                else:
                    # Default to positive if no prefix
                    sentiment_dict['positive'].add(line)

        return sentiment_dict

    def _add_custom_financial_terms(self):
        """Add custom financial terms to jieba dictionary"""
        financial_terms = [
            '资产负债表', '利润表', '现金流量表', '股东权益',
            '营业收入', '净利润', '经营活动', '投资活动',
            '筹资活动', '应收账款', '存货', '固定资产',
            '无形资产', '商誉', '长期股权投资', '短期借款',
            '长期借款', '应付账款', '预收账款', '递延所得税'
        ]

        for term in financial_terms:
            jieba.add_word(term)

    def segment_text(self, text: str) -> List[str]:
        """
        Segment Chinese text into words

        Args:
            text: Input text

        Returns:
            List of words
        """
        if self.segmentation_tool == 'jieba':
            words = jieba.lcut(text)
        else:
            # Fallback to simple character-based segmentation
            words = list(text)

        # Filter by length and remove stopwords
        words = [
            w for w in words
            if len(w) >= self.min_word_length
            and w not in self.stopwords
            and not w.isspace()
        ]

        return words

    def count_sentiment_words(self, words: List[str]) -> Tuple[int, int]:
        """
        Count positive and negative words

        Args:
            words: List of segmented words

        Returns:
            Tuple of (positive_count, negative_count)
        """
        pos_count = 0
        neg_count = 0

        for word in words:
            if word in self.sentiment_dict['positive']:
                pos_count += 1
            elif word in self.sentiment_dict['negative']:
                neg_count += 1

        return pos_count, neg_count

    def calculate_tone(self,
                       text: str,
                       words: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Calculate Tone metric (sentiment score)

        Formula (Loughran & McDonald):
            Tone = (Positive - Negative) / (Positive + Negative)

        Args:
            text: Input text
            words: Pre-segmented words, to avoid re-running jieba when the
                caller needs several metrics from the same text

        Returns:
            Dictionary with tone metrics
        """
        if words is None:
            words = self.segment_text(text)

        # Count sentiment words
        pos_count, neg_count = self.count_sentiment_words(words)

        # Calculate tone
        total_sentiment = pos_count + neg_count

        if total_sentiment == 0:
            tone_raw = 0.0
        else:
            tone_raw = (pos_count - neg_count) / total_sentiment

        result = {
            'pos_word_count': pos_count,
            'neg_word_count': neg_count,
            'total_words': len(words),
            'tone_raw': tone_raw,
            'tone_percentage': tone_raw * 100
        }

        logger.debug(f"Tone: {tone_raw:.4f} (Pos: {pos_count}, Neg: {neg_count})")

        return result

    def split_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences (Chinese)

        Args:
            text: Input text

        Returns:
            List of sentences
        """
        # Chinese sentence delimiters
        delimiters = r'[。！？；…\n]+'
        sentences = re.split(delimiters, text)

        # Remove empty sentences and whitespace
        sentences = [s.strip() for s in sentences if s.strip()]

        return sentences

    def is_complex_word(self, word: str) -> bool:
        """
        Determine if a word is complex

        Definition for Chinese:
        - More than 2 Chinese characters
        - NOT in common vocabulary list (if available)

        Args:
            word: Input word

        Returns:
            True if complex, False otherwise
        """
        # Count Chinese characters
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', word)
        char_count = len(chinese_chars)

        # If word is short, it's not complex
        if char_count <= self.complex_word_threshold:
            return False

        # If we have a common vocabulary, check against it
        if self.common_vocab:
            return word not in self.common_vocab

        # Otherwise, use character count threshold
        # Words with more than 3 characters are considered complex
        return char_count > 3

    def calculate_fog_index(self,
                            text: str,
                            words: Optional[List[str]] = None) -> Dict[str, float]:
        """
        Calculate Chinese-adapted Gunning-Fog Index

        Formula (Chinese adaptation):
            Fog = 0.4 × (average_sentence_length + percentage_complex_words)

        Where:
        - average_sentence_length = total_words / total_sentences
        - percentage_complex_words = (complex_words / total_words) × 100

        Args:
            text: Input text
            words: Pre-segmented words, to avoid re-running jieba when the
                caller needs several metrics from the same text

        Returns:
            Dictionary with readability metrics
        """
        # Split into sentences
        sentences = self.split_sentences(text)
        sentence_count = len(sentences)

        if sentence_count == 0:
            logger.warning("No sentences found in text")
            return {
                'fog_index': 0.0,
                'avg_sentence_length': 0.0,
                'complex_word_count': 0,
                'complex_word_pct': 0.0,
                'sentence_count': 0
            }

        if words is None:
            words = self.segment_text(text)
        total_words = len(words)

        # Count complex words
        complex_words = [w for w in words if self.is_complex_word(w)]
        complex_word_count = len(complex_words)

        # Calculate metrics
        avg_sentence_length = total_words / sentence_count
        complex_word_pct = (complex_word_count / total_words * 100) if total_words > 0 else 0

        # Chinese Fog Index formula
        fog_index = 0.4 * (avg_sentence_length + complex_word_pct)

        result = {
            'fog_index': fog_index,
            'avg_sentence_length': avg_sentence_length,
            'complex_word_count': complex_word_count,
            'complex_word_pct': complex_word_pct,
            'sentence_count': sentence_count,
            'total_words': total_words
        }

        logger.debug(f"Fog Index: {fog_index:.2f} (Avg sent len: {avg_sentence_length:.1f}, "
                     f"Complex: {complex_word_pct:.1f}%)")

        return result

    def calculate_alternative_metrics(self, text: str, file_path: str = None) -> Dict:
        """
        Calculate alternative readability metrics

        Args:
            text: Input text
            file_path: Optional file path for file size

        Returns:
            Dictionary with alternative metrics
        """
        metrics = {
            'char_count': len(text),
            'chinese_char_count': len(re.findall(r'[\u4e00-\u9fff]', text)),
            'file_size_bytes': 0
        }

        # File size as alternative readability proxy
        if file_path and os.path.exists(file_path):
            metrics['file_size_bytes'] = os.path.getsize(file_path)

        return metrics

    def analyze_text(self, text: str, file_path: str = None) -> Dict:
        """
        Comprehensive text analysis

        Args:
            text: Input text
            file_path: Optional file path for additional metrics

        Returns:
            Dictionary with all analysis results
        """
        result = {}

        try:
            # Segment once and share: both Tone and Fog consume the same word
            # list, and jieba over a full MD&A section dominates analysis cost.
            words = self.segment_text(text)

            # Calculate Tone
            tone_metrics = self.calculate_tone(text, words)
            result.update(tone_metrics)

            # Calculate Fog Index
            fog_metrics = self.calculate_fog_index(text, words)
            result.update(fog_metrics)

            # Alternative metrics
            alt_metrics = self.calculate_alternative_metrics(text, file_path)
            result.update(alt_metrics)

            result['success'] = True
            result['error'] = None

        except Exception as e:
            logger.error(f"Text analysis failed: {e}")
            result['success'] = False
            result['error'] = str(e)

        return result

    def batch_analyze(self, text_files: List[Tuple[str, str]]) -> List[Dict]:
        """
        Batch analyze multiple text files

        Args:
            text_files: List of (text_content, file_path) tuples

        Returns:
            List of analysis results
        """
        results = []

        logger.info(f"Batch analyzing {len(text_files)} texts")

        for text, file_path in text_files:
            result = self.analyze_text(text, file_path)
            result['file_path'] = file_path
            results.append(result)

        return results


class ChineseCommonVocabGenerator:
    """
    Helper class to generate common vocabulary list from corpus
    """

    @staticmethod
    def generate_from_corpus(corpus_files: List[str],
                             output_path: str,
                             top_n: int = 10000):
        """
        Generate common vocabulary list from corpus

        Args:
            corpus_files: List of text files
            output_path: Output path for vocabulary file
            top_n: Number of most common words to include
        """
        from collections import Counter

        word_counter = Counter()

        for file_path in corpus_files:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
                words = jieba.lcut(text)
                word_counter.update(words)

        # Get most common words
        common_words = [word for word, count in word_counter.most_common(top_n)]

        # Save to file
        with open(output_path, 'w', encoding='utf-8') as f:
            for word in common_words:
                f.write(word + '\n')

        logger.info(f"Generated common vocabulary: {len(common_words)} words")

        return common_words
