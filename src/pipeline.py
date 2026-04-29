"""
Main Pipeline Module
Orchestrates the complete analysis workflow

Usage:
    python -m src.pipeline analyze --companies data.csv --years 2020-2022
"""

import os
import sys
import argparse
import re
import hashlib
import shutil
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from loguru import logger

from .utils import (
    load_config,
    setup_directories,
    setup_logging,
    normalize_company_code,
    parse_year_range,
    create_timestamp,
    sanitize_filename
)
from .downloader import CNINFODownloader
from .pdf_parser import PDFParser
from .text_analyzer import TextAnalyzer
from .metrics import MetricsCalculator, load_financial_data_from_csv


DEFAULT_ANALYSIS_TEXT_MIN_CHARS = 500
DEFAULT_ANALYSIS_TEXT_MIN_RATIO = 0.02


def looks_like_toc_text(text: str) -> bool:
    """
    Identify short text fragments that look like table-of-contents entries.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    sample = lines[: min(len(lines), 8)]
    toc_like_count = sum(
        bool(re.search(r'[\.．·•…]{4,}\s*\d+\s*$', line))
        for line in sample
    )
    sentence_like_count = sum(bool(re.search(r'[。！？；]', line)) for line in sample)

    return (
        toc_like_count >= max(2, len(sample) // 2)
        or (toc_like_count > 0 and sentence_like_count == 0 and len(text) < 1000)
    )


def select_analysis_text(parse_result: Dict,
                         min_text_chars: int = DEFAULT_ANALYSIS_TEXT_MIN_CHARS,
                         min_mda_ratio: float = DEFAULT_ANALYSIS_TEXT_MIN_RATIO) -> Tuple[str, str]:
    """
    Choose the best text source for analysis, with guardrails for bad MD&A extraction.

    Returns:
        Tuple of (analysis_text, source_name), where source_name is mda_text,
        full_text, or an empty string when no usable text exists.
    """
    full_text = (parse_result.get('text', '') or '').strip()
    mda_text = (parse_result.get('mda_text', '') or '').strip()

    if not mda_text:
        if full_text:
            return full_text, 'full_text'
        return '', ''

    reject_reason = None
    if looks_like_toc_text(mda_text):
        reject_reason = 'looks like table of contents'
    elif len(mda_text) < min_text_chars:
        reject_reason = f'too short ({len(mda_text)} chars)'
    elif full_text and len(mda_text) / max(len(full_text), 1) < min_mda_ratio:
        reject_reason = (
            f'too small relative to full text '
            f'({len(mda_text) / max(len(full_text), 1):.3%})'
        )

    if reject_reason and full_text:
        logger.warning(f"MD&A text rejected for analysis: {reject_reason}; using full text")
        return full_text, 'full_text'

    return mda_text or full_text, 'mda_text' if mda_text else 'full_text'


class FinancialAnalysisPipeline:
    """
    End-to-end pipeline for financial report analysis
    """

    def __init__(self,
                 config_path: str = 'config.yaml',
                 sentiment_dict_path: Optional[str] = None,
                 cookies: Optional[Dict] = None):
        """
        Initialize pipeline

        Args:
            config_path: Path to configuration file
            sentiment_dict_path: Path to sentiment dictionary
            cookies: Optional cookies for authenticated downloads
        """
        # Load configuration
        self.config = load_config(config_path)

        # Setup
        setup_directories(self.config)
        setup_logging(self.config)

        # Initialize components
        if sentiment_dict_path is None:
            sentiment_dict_path = self.config.get('analyzer', {}).get(
                'sentiment_dict_path',
                'data/dictionaries/cn_financial_sentiment.txt'
            )

        self.downloader = CNINFODownloader(self.config, cookies=cookies)
        self.parser = PDFParser(self.config)
        self.analyzer = TextAnalyzer(self.config, sentiment_dict_path)
        self.metrics_calculator = MetricsCalculator(self.config)
        self.company_map: Dict[str, str] = {}
        self.last_output_file: Optional[str] = None
        analysis_config = self.config.get('analysis', {})
        stream_config = self.config.get('streaming', {})
        output_config = self.config.get('output', {})
        self.analysis_text_min_chars = analysis_config.get(
            'min_text_chars',
            DEFAULT_ANALYSIS_TEXT_MIN_CHARS
        )
        self.analysis_text_min_ratio = analysis_config.get(
            'min_mda_ratio',
            DEFAULT_ANALYSIS_TEXT_MIN_RATIO
        )
        self.save_intermediate = output_config.get('save_intermediate', False)
        self.intermediate_output_path = output_config.get(
            'intermediate_path',
            os.path.join(output_config.get('results_path', 'data/results'), 'intermediate')
        )
        self.max_saved_intermediates = output_config.get('max_saved_intermediates', 5)
        self.streaming_audit_output_path = stream_config.get(
            'audit_output_path',
            os.path.join(self.parser.output_path, 'streaming_audit')
        )
        self.streaming_max_audit_reports = stream_config.get('max_audit_reports', 10)
        Path(self.intermediate_output_path).mkdir(parents=True, exist_ok=True)
        Path(self.streaming_audit_output_path).mkdir(parents=True, exist_ok=True)

        logger.info("Financial Analysis Pipeline initialized")

    def load_company_list(self, csv_path: str) -> pd.DataFrame:
        """
        Load company list from CSV

        Expected columns: stock_code, company_name

        Args:
            csv_path: Path to CSV file

        Returns:
            DataFrame with company information
        """
        try:
            df = pd.read_csv(csv_path, encoding='utf-8-sig')
            df.columns = df.columns.str.lower().str.strip()

            # Normalize stock codes
            df['stock_code'] = df['stock_code'].apply(normalize_company_code)

            if 'company_name' in df.columns:
                self.company_map = dict(zip(df['stock_code'], df['company_name']))
            else:
                self.company_map = {}

            logger.info(f"Loaded {len(df)} companies from {csv_path}")
            return df

        except Exception as e:
            logger.error(f"Failed to load company list: {e}")
            return pd.DataFrame()

    def download_phase(self,
                       company_codes: List[str],
                       years: List[int],
                       report_types: List[str]) -> Dict:
        """
        Phase 1: Download reports

        Args:
            company_codes: List of stock codes
            years: List of years
            report_types: List of report types

        Returns:
            Dictionary of download metadata
        """
        logger.info("=" * 60)
        logger.info("PHASE 1: DOWNLOADING REPORTS")
        logger.info("=" * 60)

        metadata = self.downloader.download_reports(
            company_codes=company_codes,
            years=years,
            report_types=report_types
        )

        logger.info(f"Downloaded metadata for {len(metadata)} reports")

        return metadata

    def parse_phase(self, metadata: Dict) -> pd.DataFrame:
        """
        Phase 2: Parse PDFs

        Args:
            metadata: Download metadata dictionary

        Returns:
            DataFrame with parsing results
        """
        logger.info("=" * 60)
        logger.info("PHASE 2: PARSING PDFs")
        logger.info("=" * 60)

        parse_results = []

        for key, metas in metadata.items():
            stock_code, year, report_type = key

            if isinstance(metas, dict):
                metas = [metas]

            for meta in metas:
                file_path = meta.get('file_path')
                if not file_path or not os.path.exists(file_path):
                    logger.warning(f"File not found: {file_path}")
                    continue

                # Parse PDF
                result = self.parser.parse_pdf(file_path, save_output=True)

                # Add metadata
                result['stock_code'] = stock_code
                result['company_name'] = self.company_map.get(stock_code, '')
                result['year'] = year
                result['report_type'] = report_type
                result['file_path'] = file_path
                result['download_date'] = meta.get('date', '')

                parse_results.append(result)

        # Convert to DataFrame
        df = pd.DataFrame(parse_results)

        logger.info(f"Parsed {len(df)} reports")

        return df

    def analyze_phase(self, parse_results: pd.DataFrame) -> pd.DataFrame:
        """
        Phase 3: Analyze text (Tone and Fog)

        Args:
            parse_results: DataFrame with parsing results

        Returns:
            DataFrame with analysis results
        """
        logger.info("=" * 60)
        logger.info("PHASE 3: TEXT ANALYSIS (TONE & FOG)")
        logger.info("=" * 60)

        analysis_results = []

        for _, row in parse_results.iterrows():
            text, text_source = self._select_analysis_text(row.to_dict())

            if not text:
                logger.warning(f"No text for {row['stock_code']} ({row['year']})")
                continue

            # Analyze text
            result = self.analyzer.analyze_text(text, row['file_path'])

            # Add metadata
            result['stock_code'] = row['stock_code']
            result['company_name'] = row.get('company_name', '')
            result['year'] = row['year']
            result['report_type'] = row['report_type']
            result['file_path'] = row['file_path']
            result['text_path'] = row.get('text_path', '')
            result['download_date'] = self._normalize_download_date(
                row.get('download_date', '')
            )
            result['analysis_text_source'] = text_source

            analysis_results.append(result)

        # Convert to DataFrame
        df = pd.DataFrame(analysis_results)

        logger.info(f"Analyzed {len(df)} reports")

        return df

    @staticmethod
    def _looks_like_toc_text(text: str) -> bool:
        """
        Identify short text fragments that look like table-of-contents entries.
        """
        return looks_like_toc_text(text)

    def select_analysis_text(self, parse_result: Dict) -> Tuple[str, str]:
        """
        Choose the best text source using this pipeline's configured guardrails.
        """
        return select_analysis_text(
            parse_result,
            min_text_chars=self.analysis_text_min_chars,
            min_mda_ratio=self.analysis_text_min_ratio
        )

    def _select_analysis_text(self, parse_result: Dict) -> Tuple[str, str]:
        """
        Backward-compatible wrapper for older internal/test callers.
        """
        return self.select_analysis_text(parse_result)

    @staticmethod
    def _normalize_download_date(value) -> str:
        """
        Normalize mixed CNINFO date formats into YYYY-MM-DD strings.
        """
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ''

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ''
            if stripped.isdigit():
                value = int(stripped)
            else:
                parsed = pd.to_datetime(stripped, errors='coerce')
                if pd.notna(parsed):
                    return parsed.strftime('%Y-%m-%d')
                return stripped

        if isinstance(value, (int, float)):
            numeric = int(value)
            if numeric <= 0:
                return ''
            unit = None
            if numeric >= 10 ** 12:
                unit = 'ms'
            elif numeric >= 10 ** 9:
                unit = 's'

            if unit:
                parsed = pd.to_datetime(numeric, unit=unit, errors='coerce', utc=True)
                if pd.notna(parsed):
                    parsed = parsed.tz_convert('Asia/Shanghai')
                    return parsed.strftime('%Y-%m-%d')
            return str(numeric)

        parsed = pd.to_datetime(value, errors='coerce')
        if pd.notna(parsed):
            return parsed.strftime('%Y-%m-%d')

        return str(value)

    def _prepare_results_for_output(self, results_df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize output-critical fields before serialization.
        """
        output_df = results_df.copy()

        if 'stock_code' in output_df.columns:
            output_df['stock_code'] = output_df['stock_code'].apply(
                lambda value: normalize_company_code(value) if pd.notna(value) and str(value).strip() else ''
            )

        if 'download_date' in output_df.columns:
            output_df['download_date'] = output_df['download_date'].apply(
                self._normalize_download_date
            )

        return output_df

    def _save_intermediate_frame(self, name: str, df: pd.DataFrame) -> Optional[str]:
        """
        Persist an intermediate DataFrame for recovery/debugging.
        """
        if not self.save_intermediate:
            return None

        Path(self.intermediate_output_path).mkdir(parents=True, exist_ok=True)
        timestamp = create_timestamp()
        versioned_path = os.path.join(self.intermediate_output_path, f'{name}_{timestamp}.pkl')
        latest_path = os.path.join(self.intermediate_output_path, f'{name}_latest.pkl')

        df.to_pickle(versioned_path)
        df.to_pickle(latest_path)
        self._enforce_intermediate_retention(name)
        logger.info(f"Saved intermediate {name}: {versioned_path}")
        return versioned_path

    def _resolve_intermediate_path(self, name: str, path: Optional[str] = None) -> str:
        """
        Resolve an intermediate name/path pair to a concrete pickle path.
        """
        if path and path != 'latest':
            return path
        return os.path.join(self.intermediate_output_path, f'{name}_latest.pkl')

    def load_intermediate_frame(self,
                                name: str,
                                path: Optional[str] = None) -> pd.DataFrame:
        """
        Load a saved intermediate DataFrame by explicit path or latest snapshot.
        """
        intermediate_path = self._resolve_intermediate_path(name, path)
        if not os.path.exists(intermediate_path):
            logger.warning(f"Intermediate file not found: {intermediate_path}")
            return pd.DataFrame()

        try:
            df = pd.read_pickle(intermediate_path)
            logger.info(f"Loaded intermediate {name}: {intermediate_path}")
            return df
        except Exception as exc:
            logger.error(f"Failed to load intermediate {name}: {exc}")
            return pd.DataFrame()

    def _load_intermediate_frame(self,
                                 name: str,
                                 path: Optional[str] = None) -> pd.DataFrame:
        """
        Backward-compatible wrapper for loading intermediate DataFrames.
        """
        return self.load_intermediate_frame(name, path)

    def _enforce_intermediate_retention(self, name: str) -> None:
        """
        Keep only the most recent N timestamped intermediate snapshots per name.
        """
        max_keep = self.max_saved_intermediates
        if max_keep is None:
            return

        try:
            max_keep = int(max_keep)
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid max_saved_intermediates={max_keep}, skipping intermediate pruning"
            )
            return

        intermediate_root = Path(self.intermediate_output_path)
        snapshots = sorted(
            (
                path for path in intermediate_root.glob(f'{name}_*.pkl')
                if not path.name.endswith('_latest.pkl')
            ),
            key=lambda path: (path.stat().st_mtime, path.name)
        )

        while max_keep >= 0 and len(snapshots) > max_keep:
            oldest = snapshots.pop(0)
            oldest.unlink(missing_ok=True)
            logger.info(f"Pruned old intermediate snapshot: {oldest}")

    def _build_streaming_audit_dir_name(self,
                                        stock_code: str,
                                        year: int,
                                        report_type: str,
                                        announcement: Dict) -> str:
        """
        Build a stable, unique directory name for streaming audit artifacts.
        """
        title = announcement.get('announcementTitle', 'report')
        safe_title = sanitize_filename(title).strip('_') or 'report'
        safe_title = safe_title[:80]
        unique_source = '|'.join([
            normalize_company_code(stock_code),
            str(year),
            report_type,
            announcement.get('adjunctUrl', ''),
            str(announcement.get('announcementTime', ''))
        ])
        unique_suffix = hashlib.md5(unique_source.encode('utf-8')).hexdigest()[:8]
        return f"{normalize_company_code(stock_code)}_{year}_{report_type}_{safe_title}_{unique_suffix}"

    def _enforce_streaming_audit_retention(self) -> None:
        """
        Keep only the most recent N streaming audit directories.
        """
        max_keep = self.streaming_max_audit_reports
        if max_keep is None:
            return

        try:
            max_keep = int(max_keep)
        except (TypeError, ValueError):
            logger.warning(f"Invalid max_audit_reports={max_keep}, skipping retention pruning")
            return

        audit_root = Path(self.streaming_audit_output_path)
        if not audit_root.exists():
            return

        audit_dirs = [path for path in audit_root.iterdir() if path.is_dir()]
        audit_dirs.sort(key=lambda path: (path.stat().st_mtime, path.name))

        while max_keep >= 0 and len(audit_dirs) > max_keep:
            oldest = audit_dirs.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            logger.info(f"Pruned old streaming audit artifacts: {oldest}")

    def _relocate_streaming_audit_artifacts(self,
                                            parse_result: Dict,
                                            stock_code: str,
                                            year: int,
                                            report_type: str,
                                            announcement: Dict) -> Dict:
        """
        Move streaming parsed artifacts from the temporary parser path into
        a dedicated audit directory, then enforce capped retention.
        """
        output_dir = parse_result.get('output_dir', '')
        if not output_dir or not os.path.exists(output_dir):
            return parse_result

        audit_root = Path(self.streaming_audit_output_path)
        audit_root.mkdir(parents=True, exist_ok=True)

        target_dir = audit_root / self._build_streaming_audit_dir_name(
            stock_code, year, report_type, announcement
        )

        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.move(output_dir, target_dir)

        updated = parse_result.copy()
        updated['output_dir'] = str(target_dir)

        text_path = target_dir / 'full_text.txt'
        mda_path = target_dir / 'mda_text.txt'
        updated['text_path'] = str(text_path) if text_path.exists() else ''
        updated['mda_path'] = str(mda_path) if mda_path.exists() else ''

        self._enforce_streaming_audit_retention()
        return updated

    def finish_work(self,
                    confirm_work_complete: bool = False,
                    dry_run: bool = False) -> Dict[str, List[str]]:
        """
        Clean transient work cache after the user confirms the current run is done.

        Preserves final result exports and streaming audit artifacts. Removes
        downloaded PDFs, temporary raw files, non-audit parsed outputs, and saved
        intermediate pickle snapshots.
        """
        if not confirm_work_complete:
            raise ValueError(
                "finish_work requires confirm_work_complete=True before cache cleanup"
            )

        report: Dict[str, List[str]] = {
            'removed_files': [],
            'removed_dirs': [],
            'kept_dirs': [],
        }

        def remove_path(path: Path) -> None:
            if not path.exists():
                return

            if path.is_dir():
                report['removed_dirs'].append(str(path))
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
            else:
                report['removed_files'].append(str(path))
                if not dry_run:
                    path.unlink(missing_ok=True)

        def clean_directory_contents(root: Path,
                                     keep_paths: Optional[List[Path]] = None,
                                     keep_names: Optional[List[str]] = None) -> None:
            if not root.exists():
                return

            keep_paths = [path.resolve() for path in (keep_paths or [])]
            keep_names = keep_names or []

            for child in root.iterdir():
                if child.name in keep_names:
                    continue

                child_resolved = child.resolve()
                if any(child_resolved == keep_path for keep_path in keep_paths):
                    report['kept_dirs'].append(str(child))
                    continue

                remove_path(child)

        raw_root = Path(self.downloader.download_path)
        clean_directory_contents(raw_root, keep_names=['.gitkeep'])

        parsed_root = Path(self.parser.output_path)
        audit_root = Path(self.streaming_audit_output_path)
        clean_directory_contents(
            parsed_root,
            keep_paths=[audit_root],
            keep_names=['.gitkeep']
        )

        intermediate_root = Path(self.intermediate_output_path)
        clean_directory_contents(intermediate_root, keep_names=['.gitkeep'])

        return report

    def metrics_phase(self,
                      analysis_results: pd.DataFrame,
                      financial_data: pd.DataFrame) -> pd.DataFrame:
        """
        Phase 4: Calculate metrics (TNI)

        Args:
            analysis_results: DataFrame with analysis results
            financial_data: DataFrame with financial metrics

        Returns:
            DataFrame with all metrics
        """
        logger.info("=" * 60)
        logger.info("PHASE 4: CALCULATING METRICS (TNI)")
        logger.info("=" * 60)

        # Calculate all metrics
        metrics_df = self.metrics_calculator.calculate_all_metrics(
            analysis_results,
            financial_data
        )

        # Generate summary statistics
        summary_stats = self.metrics_calculator.generate_summary_statistics(metrics_df)

        logger.info("Summary Statistics:")
        logger.info(f"  Tone: Mean={summary_stats['tone_stats'].get('mean', 0):.4f}, "
                    f"Std={summary_stats['tone_stats'].get('std', 0):.4f}")
        logger.info(f"  TNI: Mean={summary_stats['tni_stats'].get('mean', 0):.4f}, "
                    f"Std={summary_stats['tni_stats'].get('std', 0):.4f}")

        return metrics_df

    def _rebuild_metadata_from_files(self) -> Dict:
        """
        Rebuild metadata from existing downloaded PDFs on disk.

        Returns:
            Dictionary mapping (stock_code, year, report_type) to list of metadata entries
        """
        download_root = Path(self.config['downloader']['download_path'])
        pdf_files = list(download_root.rglob('*.pdf'))
        metadata: Dict = {}

        if not pdf_files:
            logger.warning(f"No PDF files found under {download_root}")
            return metadata

        for pdf_path in pdf_files:
            # Expected structure: download_path/stock_code/year/file.pdf
            stock_code = pdf_path.parent.parent.name if pdf_path.parent.parent else ''
            try:
                year = int(pdf_path.parent.name)
            except ValueError:
                year = None

            report_type = 'unknown'
            key = (stock_code, year, report_type)
            mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime).strftime('%Y-%m-%d')
            entry = {
                'file_path': str(pdf_path),
                'url': '',
                'title': pdf_path.stem,
                'date': mtime
            }
            metadata.setdefault(key, []).append(entry)

        logger.info(f"Rebuilt metadata for {len(pdf_files)} PDFs")
        return metadata

    def _load_parsed_results(self, metadata: Dict) -> pd.DataFrame:
        """
        Load parsed results from disk using metadata.

        Args:
            metadata: Download metadata dictionary

        Returns:
            DataFrame with parsing results loaded from disk
        """
        parse_results = []

        for key, metas in metadata.items():
            stock_code, year, report_type = key

            if isinstance(metas, dict):
                metas = [metas]

            for meta in metas:
                file_path = meta.get('file_path')
                if not file_path:
                    continue

                output_dir = self.parser.output_dir_for_pdf(file_path)
                if not os.path.exists(output_dir):
                    # Backward compatibility with legacy output directory naming
                    legacy_dir = os.path.join(self.parser.output_path, Path(file_path).stem)
                    if os.path.exists(legacy_dir):
                        output_dir = legacy_dir
                text_path = os.path.join(output_dir, 'full_text.txt')
                mda_path = os.path.join(output_dir, 'mda_text.txt')

                if not os.path.exists(text_path) and not os.path.exists(mda_path):
                    logger.warning(f"Parsed text not found for {file_path}")
                    continue

                text = ''
                mda_text = ''
                if os.path.exists(text_path):
                    with open(text_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                if os.path.exists(mda_path):
                    with open(mda_path, 'r', encoding='utf-8') as f:
                        mda_text = f.read()

                result = {
                    'pdf_path': file_path,
                    'text': text,
                    'mda_text': mda_text,
                    'tables': [],
                    'financial_statements': {},
                    'text_path': text_path if os.path.exists(text_path) else '',
                    'mda_path': mda_path if os.path.exists(mda_path) else '',
                    'output_dir': output_dir,
                    'error': None
                }

                # Add metadata
                result['stock_code'] = stock_code
                result['company_name'] = self.company_map.get(stock_code, '')
                result['year'] = year
                result['report_type'] = report_type
                result['file_path'] = file_path
                result['download_date'] = meta.get('date', '')

                parse_results.append(result)

        df = pd.DataFrame(parse_results)
        logger.info(f"Loaded {len(df)} parsed reports from disk")
        return df

    def save_results(self, results_df: pd.DataFrame) -> str:
        """
        Save results to output files

        Args:
            results_df: Final results DataFrame

        Returns:
            Path to output file
        """
        logger.info("=" * 60)
        logger.info("PHASE 5: SAVING RESULTS")
        logger.info("=" * 60)

        output_path = self.config['output']['results_path']
        output_format = self.config['output']['format']

        # Create timestamp for filename
        timestamp = create_timestamp()
        prepared_df = self._prepare_results_for_output(results_df)

        # Select columns for master summary
        summary_columns = self.config['output'].get('summary_columns', [])
        if summary_columns:
            output_df = prepared_df.copy()
            for column in summary_columns:
                if column not in output_df.columns:
                    output_df[column] = pd.NA
            output_df = output_df[summary_columns]
        else:
            output_df = prepared_df

        if output_df.empty:
            logger.warning("No analysis rows available; saving empty results with schema only")

        # Save in specified format(s)
        output_files = []

        if output_format in ['excel', 'all']:
            excel_path = os.path.join(output_path, f'master_summary_{timestamp}.xlsx')
            output_df.to_excel(excel_path, index=False, engine='openpyxl')
            output_files.append(excel_path)
            logger.info(f"Saved Excel: {excel_path}")

        if output_format in ['csv', 'all']:
            csv_path = os.path.join(output_path, f'master_summary_{timestamp}.csv')
            output_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            output_files.append(csv_path)
            logger.info(f"Saved CSV: {csv_path}")

        if output_format in ['parquet', 'all']:
            parquet_path = os.path.join(output_path, f'master_summary_{timestamp}.parquet')
            output_df.to_parquet(parquet_path, index=False, engine='pyarrow')
            output_files.append(parquet_path)
            logger.info(f"Saved Parquet: {parquet_path}")

        logger.info(f"Results saved to {len(output_files)} file(s)")

        final_path = output_files[0] if output_files else None
        self.last_output_file = final_path
        return final_path

    def run(self,
            company_codes: List[str] = None,
            company_csv: str = None,
            years: List[int] = None,
            report_types: List[str] = None,
            financial_data_csv: str = None,
            skip_download: bool = False,
            skip_parse: bool = False,
            skip_analyze: bool = False,
            restore_parse_results: Optional[str] = None,
            restore_analysis_results: Optional[str] = None) -> pd.DataFrame:
        """
        Run the complete analysis pipeline (batch mode)

        Args:
            company_codes: List of stock codes (or use company_csv)
            company_csv: Path to CSV with company list
            years: List of years to analyze
            report_types: List of report types
            financial_data_csv: Path to financial data CSV
            skip_download: Skip download phase (use existing files)
            skip_parse: Skip parsing phase (use existing parsed files)
            skip_analyze: Skip analysis phase (use latest saved intermediate)
            restore_parse_results: Optional parse_results pickle path, or 'latest'
            restore_analysis_results: Optional analysis_results pickle path, or 'latest'

        Returns:
            Final results DataFrame
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("STARTING FINANCIAL ANALYSIS PIPELINE")
        logger.info("=" * 60)

        # Load company list
        if company_csv:
            company_df = self.load_company_list(company_csv)
            company_codes = company_df['stock_code'].tolist()
        elif not company_codes:
            raise ValueError("Must provide either company_codes or company_csv")

        # Default values
        if years is None:
            years = [2020, 2021, 2022]
        if report_types is None:
            report_types = ['annual']

        if restore_analysis_results:
            logger.info("Restoring analysis_results intermediate")
            analysis_results = self.load_intermediate_frame(
                'analysis_results',
                restore_analysis_results
            )
        else:
            # Phase 1/2: Download and parse, or restore parse_results directly.
            if restore_parse_results:
                logger.info("Restoring parse_results intermediate")
                parse_results = self.load_intermediate_frame(
                    'parse_results',
                    restore_parse_results
                )
            else:
                # Phase 1: Download
                if not skip_download:
                    metadata = self.download_phase(company_codes, years, report_types)
                else:
                    logger.info("Skipping download phase")
                    metadata = self._rebuild_metadata_from_files()

                # Phase 2: Parse
                if not skip_parse:
                    parse_results = self.parse_phase(metadata)
                else:
                    logger.info("Skipping parse phase")
                    parse_results = self._load_parsed_results(metadata)
                self._save_intermediate_frame('parse_results', parse_results)

            # Phase 3: Analyze
            if skip_analyze:
                logger.info("Skipping analysis phase")
                analysis_results = self._load_intermediate_frame('analysis_results')
            else:
                analysis_results = self.analyze_phase(parse_results)
                self._save_intermediate_frame('analysis_results', analysis_results)

        # Phase 4: Metrics (if financial data provided)
        if financial_data_csv:
            financial_data = load_financial_data_from_csv(financial_data_csv)
            final_results = self.metrics_phase(analysis_results, financial_data)
        else:
            logger.warning("No financial data provided, skipping TNI calculation")
            final_results = analysis_results

        # Phase 5: Save
        output_file = self.save_results(final_results)

        # Log completion
        elapsed = datetime.now() - start_time
        logger.info("=" * 60)
        logger.info(f"PIPELINE COMPLETE - Elapsed: {elapsed}")
        logger.info(f"Output: {output_file}")
        logger.info("=" * 60)

        return final_results

    # ------------------------------------------------------------------
    # Streaming mode: download → parse → analyze → delete, one at a time
    # ------------------------------------------------------------------

    def run_streaming(self,
                      company_codes: List[str] = None,
                      company_csv: str = None,
                      years: List[int] = None,
                      report_types: List[str] = None,
                      financial_data_csv: str = None,
                      delete_pdf: bool = True,
                      save_parsed_text: bool = True) -> pd.DataFrame:
        """
        Run the pipeline in streaming mode: process one PDF at a time.

        For each (company, year, report_type):
          1. Query announcement list from CNINFO API
          2. Download PDF to a temporary path
          3. Parse PDF (extract text & tables)
          4. Analyze text (Tone & Fog)
          5. Append results to accumulator
          6. Delete PDF (keep parsed text if configured)
          7. Rate-limit wait, then next

        This ensures only ~1 PDF exists on disk at any moment.

        Args:
            company_codes: List of stock codes (or use company_csv)
            company_csv: Path to CSV with company list
            years: List of years to analyze
            report_types: List of report types
            financial_data_csv: Path to financial data CSV
            delete_pdf: Delete PDF after analysis (default True)
            save_parsed_text: Save parsed text files (default True)

        Returns:
            Final results DataFrame
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("STARTING STREAMING ANALYSIS PIPELINE")
        logger.info("=" * 60)

        # Load company list
        if company_csv:
            company_df = self.load_company_list(company_csv)
            company_codes = company_df['stock_code'].tolist()
        elif not company_codes:
            raise ValueError("Must provide either company_codes or company_csv")

        # Default values
        if years is None:
            years = [2020, 2021, 2022]
        if report_types is None:
            report_types = ['annual']

        # Read streaming config
        stream_config = self.config.get('streaming', {})
        if delete_pdf is None:
            delete_pdf = stream_config.get('delete_pdf_after', True)
        if save_parsed_text is None:
            save_parsed_text = stream_config.get('save_parsed_text', True)

        # Accumulate analysis results
        all_results: List[Dict] = []

        total_tasks = len(company_codes) * len(years) * len(report_types)
        processed = 0

        logger.info(f"Streaming: {len(company_codes)} companies × "
                    f"{len(years)} years × {len(report_types)} types = "
                    f"{total_tasks} tasks")

        for stock_code in company_codes:
            for year in years:
                for report_type in report_types:
                    processed += 1
                    logger.info(f"[{processed}/{total_tasks}] "
                                f"{stock_code} / {year} / {report_type}")

                    # Step 1: Query announcements
                    announcements = self.downloader.query_announcements(
                        stock_code, year, report_type
                    )

                    if not announcements:
                        logger.warning(f"No announcements found for "
                                       f"{stock_code} ({year} {report_type})")
                        continue

                    # Process first matching announcement (the main report)
                    # Usually only one full annual report per company per year
                    for announcement in announcements[:1]:
                        result = self._process_single_report(
                            stock_code=stock_code,
                            year=year,
                            report_type=report_type,
                            announcement=announcement,
                            delete_pdf=delete_pdf,
                            save_parsed_text=save_parsed_text,
                        )
                        if result:
                            all_results.append(result)

        # Build DataFrame
        analysis_df = pd.DataFrame(all_results)
        self._save_intermediate_frame('analysis_results_streaming', analysis_df)

        # Metrics phase (if financial data provided)
        if analysis_df.empty:
            logger.warning("No results collected in streaming mode")
            final_results = analysis_df
        elif financial_data_csv:
            financial_data = load_financial_data_from_csv(financial_data_csv)
            final_results = self.metrics_phase(analysis_df, financial_data)
        else:
            logger.warning("No financial data provided, skipping TNI calculation")
            final_results = analysis_df

        # Save results
        output_file = self.save_results(final_results)

        elapsed = datetime.now() - start_time
        logger.info("=" * 60)
        logger.info(f"STREAMING PIPELINE COMPLETE - Elapsed: {elapsed}")
        logger.info(f"Processed: {len(all_results)} reports")
        logger.info(f"Output: {output_file}")
        logger.info("=" * 60)

        return final_results

    def _process_single_report(self,
                               stock_code: str,
                               year: int,
                               report_type: str,
                               announcement: Dict,
                               delete_pdf: bool,
                               save_parsed_text: bool) -> Optional[Dict]:
        """
        Process a single report: download → parse → analyze → cleanup.

        Args:
            stock_code: Stock code
            year: Report year
            report_type: Report type
            announcement: Announcement dict from CNINFO API
            delete_pdf: Whether to delete PDF after processing
            save_parsed_text: Whether to save parsed text files

        Returns:
            Analysis result dict, or None on failure
        """
        url, filename = self.downloader.build_download_url(announcement)
        if not url:
            return None

        # Temporary download path
        tmp_dir = os.path.join(self.downloader.download_path, '_streaming_tmp')
        os.makedirs(tmp_dir, exist_ok=True)
        save_path = os.path.join(tmp_dir, filename)

        try:
            # Step 2: Download
            logger.info(f"  Downloading: {filename}")
            success = self.downloader.download_one_sync(url, save_path)
            if not success:
                logger.error(f"  Download failed: {url}")
                return None

            # Step 3: Parse
            logger.info(f"  Parsing: {filename}")
            parse_result = self.parser.parse_pdf(
                save_path,
                save_output=save_parsed_text
            )
            if save_parsed_text:
                parse_result = self._relocate_streaming_audit_artifacts(
                    parse_result=parse_result,
                    stock_code=stock_code,
                    year=year,
                    report_type=report_type,
                    announcement=announcement
                )

            # Step 4: Analyze
            text, text_source = self._select_analysis_text(parse_result)
            if not text:
                logger.warning(f"  No text extracted from {filename}")
                return None

            logger.info(f"  Analyzing: {len(text)} characters")
            analysis = self.analyzer.analyze_text(text, save_path)

            # Enrich with metadata
            analysis['stock_code'] = stock_code
            analysis['company_name'] = self.company_map.get(stock_code, '')
            analysis['year'] = year
            analysis['report_type'] = report_type
            analysis['file_path'] = save_path if not delete_pdf else ''
            analysis['text_path'] = parse_result.get('text_path', '')
            analysis['download_date'] = self._normalize_download_date(
                announcement.get('announcementTime', '')
            )
            analysis['announcement_title'] = announcement.get('announcementTitle', '')
            analysis['analysis_text_source'] = text_source

            logger.info(f"  Done: Tone={analysis.get('tone_raw', 0):.4f}, "
                        f"Fog={analysis.get('fog_index', 0):.2f}")

            return analysis

        except Exception as e:
            logger.error(f"  Failed to process {filename}: {e}")
            return None

        finally:
            # Step 6: Cleanup PDF
            if delete_pdf and os.path.exists(save_path):
                os.remove(save_path)
                logger.debug(f"  Deleted PDF: {save_path}")


def main():
    """Command-line interface"""
    parser = argparse.ArgumentParser(
        description='CNINFO Financial Text Analyzer',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Download command
    download_parser = subparsers.add_parser('download', help='Download reports only')
    download_parser.add_argument('--companies', required=True, help='Path to company list CSV')
    download_parser.add_argument('--years', required=True, help='Years (e.g., 2020-2022 or 2020,2021)')
    download_parser.add_argument('--types', default='annual', help='Report types (comma-separated)')

    # Parse command
    parse_parser = subparsers.add_parser('parse', help='Parse PDFs only')
    parse_parser.add_argument('--input', required=True, help='Input directory with PDFs')
    parse_parser.add_argument('--output', default='data/parsed', help='Output directory')

    # Analyze command
    analyze_parser = subparsers.add_parser('analyze', help='Full analysis pipeline')
    analyze_parser.add_argument('--companies', required=True, help='Path to company list CSV')
    analyze_parser.add_argument('--years', required=True, help='Years (e.g., 2020-2022)')
    analyze_parser.add_argument('--types', default='annual', help='Report types')
    analyze_parser.add_argument('--financial-data', help='Path to financial data CSV')
    analyze_parser.add_argument('--skip-download', action='store_true', help='Skip download')
    analyze_parser.add_argument('--skip-parse', action='store_true', help='Skip parsing')
    analyze_parser.add_argument('--skip-analyze', action='store_true',
                                help='Skip analysis and load the latest saved intermediate')
    analyze_parser.add_argument('--restore-parse-results',
                                nargs='?',
                                const='latest',
                                default=None,
                                metavar='PICKLE',
                                help='Restore parse_results from PICKLE, or latest if omitted')
    analyze_parser.add_argument('--restore-analysis-results',
                                nargs='?',
                                const='latest',
                                default=None,
                                metavar='PICKLE',
                                help='Restore analysis_results from PICKLE, or latest if omitted')
    analyze_parser.add_argument('--streaming', action='store_true',
                                help='Use streaming mode (process one PDF at a time, '
                                     'saves disk space)')
    analyze_parser.add_argument('--keep-pdf', action='store_true',
                                help='Keep PDF files after analysis (streaming mode only)')
    analyze_parser.add_argument('--config', default='config.yaml', help='Config file path')
    analyze_parser.add_argument('--sentiment-dict',
                                default=None,
                                help='Sentiment dictionary path')

    # Finish-work command
    finish_parser = subparsers.add_parser(
        'finish-work',
        help='Confirm work is complete and clean transient cache'
    )
    finish_parser.add_argument('--confirm-work-complete',
                               action='store_true',
                               help='Required confirmation before deleting cache files')
    finish_parser.add_argument('--dry-run',
                               action='store_true',
                               help='Show what would be removed without deleting files')
    finish_parser.add_argument('--config', default='config.yaml', help='Config file path')
    finish_parser.add_argument('--sentiment-dict',
                               default=None,
                               help='Sentiment dictionary path')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    years: List[int] = []
    report_types: List[str] = []
    if args.command in {'download', 'analyze'}:
        years = parse_year_range(args.years)
        report_types = args.types.split(',')

    # Initialize pipeline
    pipeline = FinancialAnalysisPipeline(
        config_path=getattr(args, 'config', 'config.yaml'),
        sentiment_dict_path=getattr(args, 'sentiment_dict', None)
    )

    # Execute command
    if args.command == 'download':
        company_df = pipeline.load_company_list(args.companies)
        pipeline.download_phase(
            company_codes=company_df['stock_code'].tolist(),
            years=years,
            report_types=report_types
        )

    elif args.command == 'parse':
        # Find all PDFs in input directory
        pdf_files = list(Path(args.input).rglob('*.pdf'))
        logger.info(f"Found {len(pdf_files)} PDF files")
        pipeline.parser.set_output_path(args.output)
        pipeline.parser.batch_parse([str(f) for f in pdf_files])

    elif args.command == 'analyze':
        use_streaming = getattr(args, 'streaming', False)

        if use_streaming:
            results = pipeline.run_streaming(
                company_csv=args.companies,
                years=years,
                report_types=report_types,
                financial_data_csv=args.financial_data,
                delete_pdf=not getattr(args, 'keep_pdf', False),
            )
        else:
            results = pipeline.run(
                company_csv=args.companies,
                years=years,
                report_types=report_types,
                financial_data_csv=args.financial_data,
                skip_download=args.skip_download,
                skip_parse=args.skip_parse,
                skip_analyze=args.skip_analyze,
                restore_parse_results=args.restore_parse_results,
                restore_analysis_results=args.restore_analysis_results
            )
        logger.info(f"Analysis complete: {len(results)} observations")

    elif args.command == 'finish-work':
        if not args.confirm_work_complete:
            parser.error(
                "finish-work requires --confirm-work-complete to clean cache"
            )

        cleanup_report = pipeline.finish_work(
            confirm_work_complete=True,
            dry_run=args.dry_run
        )
        action = "Would remove" if args.dry_run else "Removed"
        logger.info(
            f"{action} {len(cleanup_report['removed_files'])} files and "
            f"{len(cleanup_report['removed_dirs'])} directories; "
            f"kept {len(cleanup_report['kept_dirs'])} audit directories"
        )


if __name__ == '__main__':
    main()
