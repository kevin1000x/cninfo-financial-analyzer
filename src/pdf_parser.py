"""
PDF Parser Module
Extracts text and tables from Chinese financial reports

Reference: Herrkun's pdfplumber examples for Chinese reports
"""

import os
import re
import hashlib
import shutil
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from loguru import logger
import pdfplumber


#: An MD&A candidate shorter than this is not an MD&A section.
#:
#: Measured 2026-09-03 over 74 Shanghai-main-board 2024 filings: candidates
#: that turned out to be wrong ran 15-363 characters, real MD&A sections ran
#: 12,460-110,874.  Nothing at all landed in between, so the exact value
#: inside that gap does not matter; 1000 keeps a wide margin on both sides.
#:
#: The two wrong shapes, both real:
#:   600030  a table-of-contents entry (section name followed by its page
#:           number) with no dot leaders, so `_looks_like_table_of_contents`
#:           cannot see it
#:   600035  a cross-reference inside the risk section that merely names the
#:           MD&A rather than starting it
#: In both filings the keyword occurs many times and the real section is a
#: later match -- the search loop was already right; the validator was too
#: permissive and stopped it on the first wrong hit.
MIN_MDA_CANDIDATE_CHARS = 1000


class PDFParser:
    """
    Parser for extracting text and tables from PDF reports
    Text extraction: pdfplumber (primary), OCR (fallback for scanned PDFs)
    """

    def __init__(self, config: Dict):
        """
        Initialize PDF parser

        Args:
            config: Configuration dictionary
        """
        self.config = config['parser']
        self.pdf_engine = self.config.get('pdf_engine', 'pdfplumber')
        self.output_path = self.config.get('output_path', 'data/parsed')
        self.max_saved_reports = self.config.get('max_saved_reports')
        self.use_ocr = self.config.get('use_ocr', False)
        self.extract_tables = self.config.get('extract_tables', True)
        self.table_parser = self.config.get('table_parser', 'pdfplumber')
        self.retention_exclude_dirs = set(
            self.config.get('retention_exclude_dirs', ['streaming_audit'])
        )

        # Keywords for section identification
        self.mda_keywords = self.config.get('mda_keywords', [
            '管理层讨论与分析',
            '经营情况讨论与分析'
        ])

        self.financial_keywords = self.config.get('financial_statement_keywords', {})
        output_config = config.get('output', {})
        self.save_raw_text = output_config.get('save_raw_text', True)
        self.save_structured_tables = output_config.get('save_structured_tables', True)

        Path(self.output_path).mkdir(parents=True, exist_ok=True)

        logger.info(f"PDF Parser initialized with engine: {self.pdf_engine}")

    def output_dir_for_pdf(self, pdf_path: str) -> str:
        """
        Generate a stable, collision-resistant output directory for a PDF.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Output directory path
        """
        pdf_name = Path(pdf_path).stem
        path_hash = hashlib.md5(pdf_path.encode('utf-8')).hexdigest()[:8]
        dir_name = f"{pdf_name}_{path_hash}"
        return os.path.join(self.output_path, dir_name)

    def set_output_path(self, output_path: str) -> None:
        """
        Override the parser output directory at runtime.
        """
        self.output_path = output_path
        Path(self.output_path).mkdir(parents=True, exist_ok=True)

    def extract_text_pdfplumber(self, pdf_path: str) -> str:
        """
        Extract text using pdfplumber (best for Chinese)

        Args:
            pdf_path: Path to PDF file

        Returns:
            Extracted text
        """
        text = ""

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n\n"

            logger.debug(f"Extracted {len(text)} characters from {pdf_path}")

        except Exception as e:
            logger.error(f"pdfplumber extraction failed: {e}")

        return text

    def extract_text(self, pdf_path: str) -> str:
        """
        Extract text from PDF using configured engine

        Args:
            pdf_path: Path to PDF file

        Returns:
            Extracted text
        """
        if self.pdf_engine == 'pdfplumber':
            text = self.extract_text_pdfplumber(pdf_path)
        elif self.pdf_engine in ('pymupdf', 'both'):
            # PyMuPDF 已于 2026-09-02 移除：它是 AGPL v3，而本项目声明 MIT。
            # 这里显式报出来而不是静默退回——在配置里写了这个引擎的人
            # 应当知道自己要的那条路径已经不存在了。
            logger.warning(
                f"pdf_engine='{self.pdf_engine}' 已不再支持"
                "（PyMuPDF 因 AGPL v3 与本项目的 MIT 许可证冲突而移除），改用 pdfplumber"
            )
            text = self.extract_text_pdfplumber(pdf_path)
        else:
            logger.warning(f"Unknown pdf_engine '{self.pdf_engine}', defaulting to pdfplumber")
            text = self.extract_text_pdfplumber(pdf_path)

        # OCR fallback for scanned documents
        if not text.strip() and self.use_ocr:
            logger.info("Text extraction failed, attempting OCR")
            text = self.extract_text_ocr(pdf_path)

        return text

    def extract_text_ocr(self, pdf_path: str) -> str:
        """
        Extract text using OCR (for scanned PDFs)
        Requires Tesseract installed

        Args:
            pdf_path: Path to PDF file

        Returns:
            OCR-extracted text
        """
        try:
            import pytesseract
            from pdf2image import convert_from_path

            images = convert_from_path(pdf_path)
            text = ""

            for image in images:
                text += pytesseract.image_to_string(
                    image,
                    lang=self.config.get('ocr_language', 'chi_sim')
                ) + "\n\n"

            logger.info(f"OCR extracted {len(text)} characters")
            return text

        except ImportError:
            logger.error("OCR dependencies not installed: pip install pytesseract pdf2image")
            return ""
        except Exception as e:
            logger.error(f"OCR failed: {e}")
            return ""

    def extract_mda_section(self, text: str) -> str:
        """
        Extract Management Discussion and Analysis section

        Args:
            text: Full report text

        Returns:
            MD&A section text
        """
        # Try each keyword in order, skipping table-of-contents style matches.
        for keyword in self.mda_keywords:
            for match in re.finditer(re.escape(keyword), text, re.IGNORECASE):
                start = text.rfind('\n', 0, match.start())
                start = 0 if start == -1 else start + 1
                end = self._find_section_end(text, start)
                mda_text = text[start:end].strip()

                if self._is_valid_mda_candidate(mda_text):
                    logger.debug(f"Found MD&A section: {len(mda_text)} characters")
                    return mda_text

        logger.warning("MD&A section not found, returning full text")
        return text

    def _find_section_end(self, text: str, start: int) -> int:
        """
        Find the next top-level section boundary after the current section.
        """
        boundary_pattern = re.compile(
            r'(?m)^\s*(?:第[一二三四五六七八九十百零〇两\d]+[章节][^\n]*|附件[^\n]*|财务报表[^\n]*)'
        )

        for match in boundary_pattern.finditer(text, start + 1):
            candidate_line = match.group(0).strip()
            # Skip headings that belong to the current MD&A section itself.
            if any(keyword in candidate_line for keyword in self.mda_keywords):
                continue
            return match.start()

        return len(text)

    @staticmethod
    def _is_toc_like_line(line: str) -> bool:
        """
        Detect table-of-contents style lines such as "... 22".
        """
        stripped = line.strip()
        if not stripped:
            return False
        return bool(re.search(r'[\.．·•…]{4,}\s*\d+\s*$', stripped))

    def _looks_like_table_of_contents(self, text: str) -> bool:
        """
        Reject short candidates dominated by table-of-contents formatting.
        """
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return True

        sample = lines[: min(len(lines), 8)]
        toc_like_count = sum(self._is_toc_like_line(line) for line in sample)
        sentence_like_count = sum(bool(re.search(r'[。！？；]', line)) for line in sample)

        if toc_like_count >= max(2, len(sample) // 2):
            return True

        if toc_like_count > 0 and sentence_like_count == 0 and len(text) < 1000:
            return True

        return False

    def _is_valid_mda_candidate(self, text: str) -> bool:
        """
        Validate extracted MD&A candidate before returning it.
        """
        stripped = text.strip()
        if not stripped:
            return False

        # Length first: it is the cheapest check and the one that actually
        # separates the two populations (see MIN_MDA_CANDIDATE_CHARS).
        # Returning False here lets the caller's loop keep looking, which is
        # what finds the real section.
        if len(stripped) < MIN_MDA_CANDIDATE_CHARS:
            return False

        if self._looks_like_table_of_contents(stripped):
            return False

        return True

    def extract_tables_pdfplumber(self, pdf_path: str) -> List[pd.DataFrame]:
        """
        Extract tables using pdfplumber

        Args:
            pdf_path: Path to PDF file

        Returns:
            List of DataFrames
        """
        tables = []

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    page_tables = page.extract_tables()

                    for table_num, table in enumerate(page_tables):
                        if table:
                            df = pd.DataFrame(table[1:], columns=table[0])
                            df['_page'] = page_num + 1
                            df['_table_num'] = table_num + 1
                            tables.append(df)

            logger.debug(f"Extracted {len(tables)} tables from {pdf_path}")

        except Exception as e:
            logger.error(f"Table extraction failed: {e}")

        return tables

    def extract_tables_tabula(self, pdf_path: str) -> List[pd.DataFrame]:
        """
        Extract tables using tabula-py (alternative)

        Args:
            pdf_path: Path to PDF file

        Returns:
            List of DataFrames
        """
        try:
            import tabula

            tables = tabula.read_pdf(
                pdf_path,
                pages='all',
                multiple_tables=True,
                lattice=True,
                pandas_options={'header': None}
            )

            logger.debug(f"Tabula extracted {len(tables)} tables")
            return tables

        except ImportError:
            logger.error("tabula-py not installed")
            return []
        except Exception as e:
            logger.error(f"Tabula extraction failed: {e}")
            return []

    def extract_tables_camelot(self, pdf_path: str) -> List[pd.DataFrame]:
        """
        Extract tables using camelot (alternative)

        Args:
            pdf_path: Path to PDF file

        Returns:
            List of DataFrames
        """
        try:
            import camelot

            tables = camelot.read_pdf(pdf_path, pages='all')
            dataframes = [t.df for t in tables]
            logger.debug(f"Camelot extracted {len(dataframes)} tables")
            return dataframes

        except ImportError:
            logger.error("camelot-py not installed")
            return []
        except Exception as e:
            logger.error(f"Camelot extraction failed: {e}")
            return []

    def extract_tables_by_engine(self, pdf_path: str) -> List[pd.DataFrame]:
        """
        Extract tables using configured engine

        Args:
            pdf_path: Path to PDF file

        Returns:
            List of DataFrames
        """
        if self.table_parser == 'pdfplumber':
            return self.extract_tables_pdfplumber(pdf_path)
        if self.table_parser == 'tabula':
            return self.extract_tables_tabula(pdf_path)
        if self.table_parser == 'camelot':
            return self.extract_tables_camelot(pdf_path)

        logger.warning(f"Unknown table_parser '{self.table_parser}', defaulting to pdfplumber")
        return self.extract_tables_pdfplumber(pdf_path)

    def identify_financial_statement(self, table: pd.DataFrame,
                                     statement_type: str) -> bool:
        """
        Identify if table is a specific financial statement

        Args:
            table: DataFrame to check
            statement_type: 'balance_sheet', 'income_statement', 'cash_flow'

        Returns:
            True if matches statement type
        """
        keywords = self.financial_keywords.get(statement_type, [])

        # Check if any keyword appears in table text
        table_text = table.astype(str).to_string()

        for keyword in keywords:
            if keyword in table_text:
                return True

        return False

    def extract_financial_statements(self, tables: List[pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        Extract and classify financial statements

        Args:
            tables: List of all extracted tables

        Returns:
            Dictionary of statement_type -> DataFrame
        """
        statements = {}

        for table in tables:
            if self.identify_financial_statement(table, 'balance_sheet'):
                statements['balance_sheet'] = table
            elif self.identify_financial_statement(table, 'income_statement'):
                statements['income_statement'] = table
            elif self.identify_financial_statement(table, 'cash_flow'):
                statements['cash_flow'] = table

        logger.info(f"Identified {len(statements)} financial statements")
        return statements

    def parse_pdf(self, pdf_path: str, save_output: bool = True) -> Dict:
        """
        Main parsing function for a single PDF

        Args:
            pdf_path: Path to PDF file
            save_output: Whether to save extracted data

        Returns:
            Dictionary containing extracted text and tables
        """
        result = {
            'pdf_path': pdf_path,
            'text': '',
            'mda_text': '',
            'tables': [],
            'financial_statements': {},
            'text_path': '',
            'mda_path': '',
            'output_dir': '',
            'error': None
        }

        try:
            # Extract text
            logger.info(f"Parsing PDF: {pdf_path}")
            text = self.extract_text(pdf_path)
            result['text'] = text

            # Extract MD&A section
            if text:
                mda_text = self.extract_mda_section(text)
                result['mda_text'] = mda_text

            # Extract tables
            if self.extract_tables:
                tables = self.extract_tables_by_engine(pdf_path)
                result['tables'] = tables

                # Identify financial statements
                if tables:
                    statements = self.extract_financial_statements(tables)
                    result['financial_statements'] = statements

            # Save output
            if save_output:
                paths = self.save_parsed_data(pdf_path, result)
                result.update(paths)

            logger.info(f"Successfully parsed {pdf_path}")

        except Exception as e:
            logger.error(f"Failed to parse {pdf_path}: {e}")
            result['error'] = str(e)

        return result

    def save_parsed_data(self, pdf_path: str, result: Dict) -> Dict[str, str]:
        """
        Save parsed data to files

        Args:
            pdf_path: Original PDF path
            result: Parsing result dictionary
        """
        # Create output directory based on PDF structure
        output_dir = self.output_dir_for_pdf(pdf_path)
        os.makedirs(output_dir, exist_ok=True)

        text_path = ''
        mda_path = ''

        if self.save_raw_text:
            text_path = os.path.join(output_dir, 'full_text.txt')
            with open(text_path, 'w', encoding='utf-8') as f:
                f.write(result['text'])

            mda_path = os.path.join(output_dir, 'mda_text.txt')
            with open(mda_path, 'w', encoding='utf-8') as f:
                f.write(result['mda_text'])

        if self.save_structured_tables:
            for i, table in enumerate(result['tables']):
                table_path = os.path.join(output_dir, f'table_{i + 1}.csv')
                table.to_csv(table_path, index=False, encoding='utf-8-sig')

            for statement_type, df in result['financial_statements'].items():
                statement_path = os.path.join(output_dir, f'{statement_type}.csv')
                df.to_csv(statement_path, index=False, encoding='utf-8-sig')

        self._enforce_output_retention()

        logger.debug(f"Saved parsed data to {output_dir}")

        return {
            'text_path': text_path,
            'mda_path': mda_path,
            'output_dir': output_dir
        }

    def _enforce_output_retention(self) -> None:
        """
        Keep only the most recent N parsed-report directories.
        """
        max_keep = self.max_saved_reports
        if max_keep is None:
            return

        try:
            max_keep = int(max_keep)
        except (TypeError, ValueError):
            logger.warning(f"Invalid max_saved_reports={max_keep}, skipping parser retention")
            return

        output_root = Path(self.output_path)
        parsed_dirs = [
            path for path in output_root.iterdir()
            if path.is_dir()
            and not path.name.startswith('.')
            and path.name not in self.retention_exclude_dirs
        ]
        parsed_dirs.sort(key=lambda path: (path.stat().st_mtime, path.name))

        while max_keep >= 0 and len(parsed_dirs) > max_keep:
            oldest = parsed_dirs.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            logger.info(f"Pruned old parsed artifacts: {oldest}")

    def batch_parse(self, pdf_files: List[str]) -> List[Dict]:
        """
        Parse multiple PDF files

        Args:
            pdf_files: List of PDF file paths

        Returns:
            List of parsing results
        """
        results = []

        logger.info(f"Batch parsing {len(pdf_files)} PDFs")

        for pdf_path in pdf_files:
            result = self.parse_pdf(pdf_path)
            results.append(result)

        return results
