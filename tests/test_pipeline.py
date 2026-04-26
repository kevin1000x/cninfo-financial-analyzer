"""
Unit tests for pipeline output-quality safeguards.
"""

import os
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.pipeline import FinancialAnalysisPipeline, select_analysis_text


class StubAnalyzer:
    """Capture analyze_text inputs while returning a minimal result payload."""

    def __init__(self):
        self.calls = []

    def analyze_text(self, text, file_path=None):
        self.calls.append((text, file_path))
        return {
            'tone_raw': 0.1,
            'fog_index': 3.2,
            'pos_word_count': 1,
            'neg_word_count': 0,
            'total_words': 10,
            'avg_sentence_length': 5.0,
            'complex_word_count': 2,
            'complex_word_pct': 20.0,
            'sentence_count': 2,
            'char_count': len(text),
            'file_size_bytes': 128,
            'success': True,
            'error': None,
        }


class StubStreamingDownloader:
    """Small downloader double that creates a local PDF placeholder."""

    def __init__(self, download_path):
        self.download_path = str(download_path)
        self.downloaded_paths = []

    def build_download_url(self, announcement):
        return 'https://example.com/report.pdf', announcement.get('filename', 'report.pdf')

    def download_one_sync(self, url, save_path):
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_bytes(b'%PDF-1.4\n% streaming test placeholder\n')
        self.downloaded_paths.append(save_path)
        return True


class StubStreamingParser:
    """Parser double that records save_output and returns generated parse results."""

    def __init__(self, result_factory):
        self.result_factory = result_factory
        self.calls = []

    def parse_pdf(self, pdf_path, save_output=True):
        self.calls.append((pdf_path, save_output))
        return self.result_factory(pdf_path, save_output)


@pytest.fixture
def pipeline_stub(tmp_path):
    """Create a lightweight pipeline instance without full initialization."""
    pipeline = FinancialAnalysisPipeline.__new__(FinancialAnalysisPipeline)
    pipeline.config = {
        'output': {
            'results_path': str(tmp_path),
            'format': 'excel',
            'summary_columns': [
                'stock_code',
                'company_name',
                'year',
                'report_type',
                'file_path',
                'text_path',
                'analysis_text_source',
                'download_date',
                'tone_raw',
                'fog_index',
            ],
        }
    }
    pipeline.analyzer = StubAnalyzer()
    pipeline.analysis_text_min_chars = 500
    pipeline.analysis_text_min_ratio = 0.02
    pipeline.save_intermediate = False
    pipeline.intermediate_output_path = str(tmp_path / 'intermediate')
    pipeline.max_saved_intermediates = 5
    pipeline.streaming_audit_output_path = str(tmp_path / 'streaming_audit')
    pipeline.streaming_max_audit_reports = 10
    pipeline.company_map = {}
    return pipeline


def _good_streaming_texts():
    mda_text = "第三章 管理层讨论与分析\n" + ("经营情况良好，营业收入持续增长。" * 100)
    full_text = mda_text + "\n" + ("这是完整报告正文。" * 200)
    return full_text, mda_text


def _streaming_announcement(idx=0):
    return {
        'announcementTitle': f'2023年年度报告_{idx}',
        'adjunctUrl': f'finalpage/2024-03-15/{idx}.PDF',
        'announcementTime': 1710432000000 + idx,
        'filename': f'report_{idx}.pdf',
    }


def test_analyze_phase_falls_back_to_full_text_for_bad_mda(pipeline_stub):
    """Short TOC-like MD&A text should not drive final metrics."""
    full_text = "第三章 管理层讨论与分析\n" + ("这是实际正文内容。" * 200)
    parse_results = pd.DataFrame([
        {
            'stock_code': '000001',
            'company_name': '平安银行',
            'year': 2023,
            'report_type': 'annual',
            'file_path': 'data/raw/report.pdf',
            'text_path': 'data/parsed/full_text.txt',
            'download_date': 1710432000000,
            'text': full_text,
            'mda_text': '管理层讨论与分析 ................................ 22\n3.1 总体经营情况 ........................... 22',
        }
    ])

    results = pipeline_stub.analyze_phase(parse_results)

    assert len(results) == 1
    assert pipeline_stub.analyzer.calls[0][0] == full_text
    assert results.iloc[0]['analysis_text_source'] == 'full_text'
    assert results.iloc[0]['download_date'] == '2024-03-15'


def test_select_analysis_text_uses_valid_mda_text():
    """Reusable text selector should prefer valid MD&A content."""
    full_text = "第一章 公司情况\n" + ("其他正文。" * 300)
    mda_text = "第三章 管理层讨论与分析\n" + ("经营情况良好，收入持续增长。" * 80)

    text, source = select_analysis_text({
        'text': full_text,
        'mda_text': mda_text,
    })

    assert text == mda_text
    assert source == 'mda_text'


def test_select_analysis_text_falls_back_for_toc_mda():
    """TOC-like MD&A snippets should fall back to full text."""
    full_text = "第三章 管理层讨论与分析\n" + ("这是完整正文内容。" * 200)

    text, source = select_analysis_text({
        'text': full_text,
        'mda_text': '管理层讨论与分析 ................................ 22\n3.1 总体经营情况 ........................... 22',
    })

    assert text == full_text
    assert source == 'full_text'


def test_select_analysis_text_falls_back_for_short_mda():
    """Short non-TOC MD&A text should fall back when full text is available."""
    full_text = "第三章 管理层讨论与分析\n" + ("这是完整正文内容。" * 200)
    short_mda = "管理层讨论与分析。本年收入增长。"

    text, source = select_analysis_text({
        'text': full_text,
        'mda_text': short_mda,
    })

    assert text == full_text
    assert source == 'full_text'


def test_select_analysis_text_falls_back_for_low_mda_ratio():
    """Tiny MD&A slices relative to full text should fall back to full text."""
    full_text = "第三章 管理层讨论与分析\n" + ("这是完整正文内容。" * 5000)
    mda_text = "第三章 管理层讨论与分析\n" + ("经营情况良好。" * 80)

    text, source = select_analysis_text({
        'text': full_text,
        'mda_text': mda_text,
    })

    assert text == full_text
    assert source == 'full_text'


def test_pipeline_public_text_selector_uses_configured_thresholds(pipeline_stub):
    """External callers can reuse the pipeline-configured text selector."""
    pipeline_stub.analysis_text_min_chars = 20
    pipeline_stub.analysis_text_min_ratio = 0.5
    full_text = "第三章 管理层讨论与分析\n" + ("完整正文。" * 100)
    mda_text = "第三章 管理层讨论与分析\n" + ("经营良好。" * 10)

    text, source = pipeline_stub.select_analysis_text({
        'text': full_text,
        'mda_text': mda_text,
    })

    assert text == full_text
    assert source == 'full_text'


def test_save_results_normalizes_exported_types(pipeline_stub, monkeypatch):
    """Exported workbook should preserve padded stock codes and readable dates."""
    monkeypatch.setattr('src.pipeline.create_timestamp', lambda: '20240101_120000')
    results_df = pd.DataFrame([
        {
            'stock_code': 1,
            'company_name': '平安银行',
            'year': 2023,
            'report_type': 'annual',
            'file_path': 'data/raw/report.pdf',
            'text_path': 'data/parsed/full_text.txt',
            'analysis_text_source': 'full_text',
            'download_date': 1710432000000,
            'tone_raw': 0.1,
            'fog_index': 3.2,
        }
    ])

    output_path = pipeline_stub.save_results(results_df)

    assert os.path.exists(output_path)
    workbook = load_workbook(output_path, read_only=True, data_only=True)
    sheet = workbook.active
    headers = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    values = [cell.value for cell in next(sheet.iter_rows(min_row=2, max_row=2))]
    types = [cell.data_type for cell in next(sheet.iter_rows(min_row=2, max_row=2))]

    stock_idx = headers.index('stock_code')
    date_idx = headers.index('download_date')
    assert values[stock_idx] == '000001'
    assert types[stock_idx] == 's'
    assert values[date_idx] == '2024-03-15'


def test_save_results_writes_schema_for_empty_dataframe(pipeline_stub, monkeypatch):
    """Even empty exports should keep a stable schema for downstream use."""
    monkeypatch.setattr('src.pipeline.create_timestamp', lambda: '20240101_120001')

    output_path = pipeline_stub.save_results(pd.DataFrame())

    exported = pd.read_excel(output_path)
    assert list(exported.columns) == pipeline_stub.config['output']['summary_columns']
    assert exported.empty


def test_streaming_audit_retention_keeps_latest_dirs(pipeline_stub, tmp_path):
    """Streaming audit artifacts should be capped to the configured retention size."""
    pipeline_stub.streaming_audit_output_path = str(tmp_path / 'audit_root')
    pipeline_stub.streaming_max_audit_reports = 2

    results = []
    for idx in range(3):
        source_dir = tmp_path / f'tmp_parse_{idx}'
        source_dir.mkdir()
        (source_dir / 'full_text.txt').write_text(f'full-{idx}', encoding='utf-8')
        (source_dir / 'mda_text.txt').write_text(f'mda-{idx}', encoding='utf-8')

        parse_result = {
            'output_dir': str(source_dir),
            'text_path': str(source_dir / 'full_text.txt'),
            'mda_path': str(source_dir / 'mda_text.txt'),
        }

        relocated = pipeline_stub._relocate_streaming_audit_artifacts(
            parse_result=parse_result,
            stock_code=f'{idx + 1:06d}',
            year=2023,
            report_type='annual',
            announcement={
                'announcementTitle': f'2023年年度报告_{idx}',
                'adjunctUrl': f'finalpage/2024-03-15/{idx}.PDF',
                'announcementTime': 1710432000000 + idx,
            }
        )
        results.append(relocated)

        if idx == 0:
            oldest_dir = Path(relocated['output_dir'])
            os.utime(oldest_dir, (1, 1))
        elif idx == 1:
            middle_dir = Path(relocated['output_dir'])
            os.utime(middle_dir, (2, 2))

    audit_dirs = sorted(
        path.name for path in Path(pipeline_stub.streaming_audit_output_path).iterdir()
        if path.is_dir()
    )

    assert len(audit_dirs) == 2
    assert not Path(results[0]['output_dir']).exists()
    assert Path(results[1]['output_dir']).exists()
    assert Path(results[2]['output_dir']).exists()


def test_streaming_save_parsed_text_false_leaves_no_audit_dir(pipeline_stub, tmp_path):
    """Without parsed-text persistence, streaming should not leave audit artifacts."""
    pipeline_stub.streaming_audit_output_path = str(tmp_path / 'audit_root')
    pipeline_stub.downloader = StubStreamingDownloader(tmp_path / 'downloads')
    full_text, mda_text = _good_streaming_texts()

    pipeline_stub.parser = StubStreamingParser(
        lambda _pdf_path, _save_output: {
            'text': full_text,
            'mda_text': mda_text,
            'text_path': '',
            'mda_path': '',
            'output_dir': '',
        }
    )

    result = pipeline_stub._process_single_report(
        stock_code='000001',
        year=2023,
        report_type='annual',
        announcement=_streaming_announcement(),
        delete_pdf=True,
        save_parsed_text=False,
    )

    assert result is not None
    assert pipeline_stub.parser.calls[0][1] is False
    assert result['text_path'] == ''
    assert result['file_path'] == ''
    audit_root = Path(pipeline_stub.streaming_audit_output_path)
    assert not audit_root.exists() or not any(audit_root.iterdir())
    assert not Path(pipeline_stub.downloader.downloaded_paths[0]).exists()


def test_streaming_raw_text_disabled_keeps_audit_dir_without_txt(pipeline_stub, tmp_path):
    """If parser saves an audit directory without raw text, relocation should preserve that."""
    pipeline_stub.streaming_audit_output_path = str(tmp_path / 'audit_root')
    pipeline_stub.downloader = StubStreamingDownloader(tmp_path / 'downloads')
    full_text, mda_text = _good_streaming_texts()

    def parse_result(_pdf_path, _save_output):
        source_dir = tmp_path / 'parser_output_no_txt'
        source_dir.mkdir()
        (source_dir / 'table_1.csv').write_text('col\nvalue\n', encoding='utf-8')
        return {
            'text': full_text,
            'mda_text': mda_text,
            'text_path': '',
            'mda_path': '',
            'output_dir': str(source_dir),
        }

    pipeline_stub.parser = StubStreamingParser(parse_result)

    result = pipeline_stub._process_single_report(
        stock_code='000001',
        year=2023,
        report_type='annual',
        announcement=_streaming_announcement(),
        delete_pdf=True,
        save_parsed_text=True,
    )

    audit_dirs = [path for path in Path(pipeline_stub.streaming_audit_output_path).iterdir()]
    assert result is not None
    assert pipeline_stub.parser.calls[0][1] is True
    assert len(audit_dirs) == 1
    audit_dir = audit_dirs[0]
    assert audit_dir.is_dir()
    assert not (audit_dir / 'full_text.txt').exists()
    assert not (audit_dir / 'mda_text.txt').exists()
    assert (audit_dir / 'table_1.csv').exists()
    assert result['text_path'] == ''


def test_streaming_structured_tables_disabled_leaves_no_csv(pipeline_stub, tmp_path):
    """When parser does not save structured tables, streaming audit should not invent CSVs."""
    pipeline_stub.streaming_audit_output_path = str(tmp_path / 'audit_root')
    pipeline_stub.downloader = StubStreamingDownloader(tmp_path / 'downloads')
    full_text, mda_text = _good_streaming_texts()

    def parse_result(_pdf_path, _save_output):
        source_dir = tmp_path / 'parser_output_no_tables'
        source_dir.mkdir()
        (source_dir / 'full_text.txt').write_text(full_text, encoding='utf-8')
        (source_dir / 'mda_text.txt').write_text(mda_text, encoding='utf-8')
        return {
            'text': full_text,
            'mda_text': mda_text,
            'text_path': str(source_dir / 'full_text.txt'),
            'mda_path': str(source_dir / 'mda_text.txt'),
            'output_dir': str(source_dir),
        }

    pipeline_stub.parser = StubStreamingParser(parse_result)

    result = pipeline_stub._process_single_report(
        stock_code='000001',
        year=2023,
        report_type='annual',
        announcement=_streaming_announcement(),
        delete_pdf=True,
        save_parsed_text=True,
    )

    audit_dirs = [path for path in Path(pipeline_stub.streaming_audit_output_path).iterdir()]
    assert result is not None
    assert len(audit_dirs) == 1
    assert not list(audit_dirs[0].glob('*.csv'))
    assert Path(result['text_path']).exists()


def test_streaming_retention_still_applies_from_single_report_path(pipeline_stub, tmp_path):
    """Retention should still cap audit dirs when processing reports one by one."""
    pipeline_stub.streaming_audit_output_path = str(tmp_path / 'audit_root')
    pipeline_stub.streaming_max_audit_reports = 2
    pipeline_stub.downloader = StubStreamingDownloader(tmp_path / 'downloads')
    full_text, mda_text = _good_streaming_texts()
    counter = {'value': 0}

    def parse_result(_pdf_path, _save_output):
        idx = counter['value']
        counter['value'] += 1
        source_dir = tmp_path / f'parser_output_{idx}'
        source_dir.mkdir()
        (source_dir / 'full_text.txt').write_text(full_text, encoding='utf-8')
        (source_dir / 'mda_text.txt').write_text(mda_text, encoding='utf-8')
        return {
            'text': full_text,
            'mda_text': mda_text,
            'text_path': str(source_dir / 'full_text.txt'),
            'mda_path': str(source_dir / 'mda_text.txt'),
            'output_dir': str(source_dir),
        }

    pipeline_stub.parser = StubStreamingParser(parse_result)
    outputs = []
    for idx in range(3):
        result = pipeline_stub._process_single_report(
            stock_code=f'{idx + 1:06d}',
            year=2023,
            report_type='annual',
            announcement=_streaming_announcement(idx),
            delete_pdf=True,
            save_parsed_text=True,
        )
        outputs.append(result)
        if idx == 0:
            os.utime(outputs[0]['text_path'], (1, 1))
            os.utime(Path(outputs[0]['text_path']).parent, (1, 1))
        elif idx == 1:
            os.utime(outputs[1]['text_path'], (2, 2))
            os.utime(Path(outputs[1]['text_path']).parent, (2, 2))

    audit_dirs = [path for path in Path(pipeline_stub.streaming_audit_output_path).iterdir()]
    assert len(audit_dirs) == 2
    assert not Path(outputs[0]['text_path']).parent.exists()
    assert Path(outputs[1]['text_path']).parent.exists()
    assert Path(outputs[2]['text_path']).parent.exists()


def test_streaming_keep_pdf_preserves_file_path_and_pdf(pipeline_stub, tmp_path):
    """delete_pdf=False should keep the PDF path in results and leave the file on disk."""
    pipeline_stub.streaming_audit_output_path = str(tmp_path / 'audit_root')
    pipeline_stub.downloader = StubStreamingDownloader(tmp_path / 'downloads')
    full_text, mda_text = _good_streaming_texts()
    pipeline_stub.parser = StubStreamingParser(
        lambda _pdf_path, _save_output: {
            'text': full_text,
            'mda_text': mda_text,
            'text_path': '',
            'mda_path': '',
            'output_dir': '',
        }
    )

    result = pipeline_stub._process_single_report(
        stock_code='000001',
        year=2023,
        report_type='annual',
        announcement=_streaming_announcement(),
        delete_pdf=False,
        save_parsed_text=False,
    )

    assert result is not None
    assert result['file_path'] == pipeline_stub.downloader.downloaded_paths[0]
    assert Path(result['file_path']).exists()


def test_intermediate_frames_can_be_saved_and_loaded(pipeline_stub, monkeypatch):
    """Intermediate snapshots should be recoverable for later reruns."""
    pipeline_stub.save_intermediate = True
    monkeypatch.setattr('src.pipeline.create_timestamp', lambda: '20240101_120002')

    frame = pd.DataFrame([{'stock_code': '000001', 'tone_raw': 0.1}])
    saved_path = pipeline_stub._save_intermediate_frame('analysis_results', frame)
    loaded = pipeline_stub.load_intermediate_frame('analysis_results', 'latest')

    assert saved_path is not None
    assert os.path.exists(saved_path)
    assert loaded.equals(frame)


def test_intermediate_frame_can_be_loaded_from_explicit_path(pipeline_stub, tmp_path):
    """Intermediate recovery should support a specific pickle path."""
    frame = pd.DataFrame([{'stock_code': '000002', 'tone_raw': -0.2}])
    explicit_path = tmp_path / 'custom_analysis_results.pkl'
    frame.to_pickle(explicit_path)

    loaded = pipeline_stub.load_intermediate_frame(
        'analysis_results',
        str(explicit_path)
    )

    assert loaded.equals(frame)


def test_intermediate_frame_missing_file_returns_empty(pipeline_stub, tmp_path):
    """Missing intermediate files should return an empty DataFrame."""
    loaded = pipeline_stub.load_intermediate_frame(
        'analysis_results',
        str(tmp_path / 'missing.pkl')
    )

    assert loaded.empty


def test_run_restore_parse_results_skips_download_and_parse(pipeline_stub, tmp_path):
    """Explicit parse_results restore should bypass download/parse and then analyze."""
    parse_frame = pd.DataFrame([
        {
            'stock_code': '000001',
            'company_name': '平安银行',
            'year': 2023,
            'report_type': 'annual',
            'file_path': 'report.pdf',
            'text': '第三章 管理层讨论与分析\n' + ('经营良好。' * 200),
            'mda_text': '第三章 管理层讨论与分析\n' + ('经营良好。' * 100),
            'download_date': '2024-03-15',
        }
    ])
    parse_path = tmp_path / 'parse_results.pkl'
    parse_frame.to_pickle(parse_path)
    analysis_frame = pd.DataFrame([{'stock_code': '000001', 'tone_raw': 0.1}])

    pipeline_stub.download_phase = Mock(return_value={})
    pipeline_stub.parse_phase = Mock(return_value=pd.DataFrame())
    pipeline_stub.analyze_phase = Mock(return_value=analysis_frame)
    pipeline_stub.save_results = Mock(return_value='out.xlsx')

    result = pipeline_stub.run(
        company_codes=['000001'],
        years=[2023],
        restore_parse_results=str(parse_path),
    )

    pipeline_stub.download_phase.assert_not_called()
    pipeline_stub.parse_phase.assert_not_called()
    pipeline_stub.analyze_phase.assert_called_once()
    pd.testing.assert_frame_equal(
        pipeline_stub.analyze_phase.call_args.args[0],
        parse_frame
    )
    assert result.equals(analysis_frame)


def test_run_restore_analysis_results_skips_earlier_phases(pipeline_stub, tmp_path):
    """Explicit analysis_results restore should bypass download, parse, and analyze."""
    analysis_frame = pd.DataFrame([{'stock_code': '000001', 'tone_raw': 0.2}])
    analysis_path = tmp_path / 'analysis_results.pkl'
    analysis_frame.to_pickle(analysis_path)

    pipeline_stub.download_phase = Mock(return_value={})
    pipeline_stub.parse_phase = Mock(return_value=pd.DataFrame())
    pipeline_stub.analyze_phase = Mock(return_value=pd.DataFrame())
    pipeline_stub.save_results = Mock(return_value='out.xlsx')

    result = pipeline_stub.run(
        company_codes=['000001'],
        years=[2023],
        restore_analysis_results=str(analysis_path),
    )

    pipeline_stub.download_phase.assert_not_called()
    pipeline_stub.parse_phase.assert_not_called()
    pipeline_stub.analyze_phase.assert_not_called()
    assert result.equals(analysis_frame)


def test_run_skip_flags_with_skip_analyze_keep_existing_flow(pipeline_stub, tmp_path):
    """Legacy skip flags should still rebuild/load parsed data before loading latest analysis."""
    parse_frame = pd.DataFrame([{'stock_code': '000001', 'text': '正文'}])
    analysis_frame = pd.DataFrame([{'stock_code': '000001', 'tone_raw': 0.3}])
    Path(pipeline_stub.intermediate_output_path).mkdir(parents=True, exist_ok=True)
    analysis_frame.to_pickle(
        Path(pipeline_stub.intermediate_output_path) / 'analysis_results_latest.pkl'
    )

    metadata = {('000001', 2023, 'annual'): [{'file_path': 'report.pdf'}]}
    pipeline_stub._rebuild_metadata_from_files = Mock(return_value=metadata)
    pipeline_stub._load_parsed_results = Mock(return_value=parse_frame)
    pipeline_stub.download_phase = Mock(return_value={})
    pipeline_stub.parse_phase = Mock(return_value=pd.DataFrame())
    pipeline_stub.analyze_phase = Mock(return_value=pd.DataFrame())
    pipeline_stub.save_results = Mock(return_value='out.xlsx')

    result = pipeline_stub.run(
        company_codes=['000001'],
        years=[2023],
        skip_download=True,
        skip_parse=True,
        skip_analyze=True,
    )

    pipeline_stub._rebuild_metadata_from_files.assert_called_once()
    pipeline_stub._load_parsed_results.assert_called_once_with(metadata)
    pipeline_stub.download_phase.assert_not_called()
    pipeline_stub.parse_phase.assert_not_called()
    pipeline_stub.analyze_phase.assert_not_called()
    assert result.equals(analysis_frame)


def test_parse_cli_respects_output_override(monkeypatch, tmp_path):
    """CLI parse command should direct outputs to the requested folder."""
    parser_mock = Mock()
    parser_mock.set_output_path = Mock()
    parser_mock.batch_parse = Mock()

    pipeline_instance = Mock()
    pipeline_instance.parser = parser_mock

    pipeline_constructor = Mock(return_value=pipeline_instance)
    monkeypatch.setattr('src.pipeline.FinancialAnalysisPipeline', pipeline_constructor)
    monkeypatch.setattr('src.pipeline.Path.rglob', Mock(return_value=[tmp_path / 'sample.pdf']))
    monkeypatch.setattr(
        'sys.argv',
        [
            'pipeline.py',
            'parse',
            '--input',
            str(tmp_path),
            '--output',
            str(tmp_path / 'parsed_out'),
        ]
    )

    from src.pipeline import main

    main()

    parser_mock.set_output_path.assert_called_once_with(str(tmp_path / 'parsed_out'))
    parser_mock.batch_parse.assert_called_once_with([str(tmp_path / 'sample.pdf')])
    pipeline_constructor.assert_called_once_with(
        config_path='config.yaml',
        sentiment_dict_path=None
    )


def test_analyze_cli_passes_explicit_sentiment_dict(monkeypatch, tmp_path):
    """CLI should only override the config sentiment dictionary when requested."""
    pipeline_instance = Mock()
    pipeline_instance.run.return_value = pd.DataFrame()

    pipeline_constructor = Mock(return_value=pipeline_instance)
    monkeypatch.setattr('src.pipeline.FinancialAnalysisPipeline', pipeline_constructor)
    monkeypatch.setattr(
        'sys.argv',
        [
            'pipeline.py',
            'analyze',
            '--companies',
            str(tmp_path / 'companies.csv'),
            '--years',
            '2023',
            '--config',
            str(tmp_path / 'custom.yaml'),
            '--sentiment-dict',
            str(tmp_path / 'custom_dict.txt'),
        ]
    )

    from src.pipeline import main

    main()

    pipeline_constructor.assert_called_once_with(
        config_path=str(tmp_path / 'custom.yaml'),
        sentiment_dict_path=str(tmp_path / 'custom_dict.txt')
    )


def test_analyze_cli_passes_intermediate_restore_args(monkeypatch, tmp_path):
    """CLI should expose explicit intermediate restore controls."""
    pipeline_instance = Mock()
    pipeline_instance.run.return_value = pd.DataFrame()

    pipeline_constructor = Mock(return_value=pipeline_instance)
    monkeypatch.setattr('src.pipeline.FinancialAnalysisPipeline', pipeline_constructor)
    monkeypatch.setattr(
        'sys.argv',
        [
            'pipeline.py',
            'analyze',
            '--companies',
            str(tmp_path / 'companies.csv'),
            '--years',
            '2023',
            '--restore-parse-results',
            str(tmp_path / 'parse_results.pkl'),
            '--restore-analysis-results',
        ]
    )

    from src.pipeline import main

    main()

    pipeline_instance.run.assert_called_once_with(
        company_csv=str(tmp_path / 'companies.csv'),
        years=[2023],
        report_types=['annual'],
        financial_data_csv=None,
        skip_download=False,
        skip_parse=False,
        skip_analyze=False,
        restore_parse_results=str(tmp_path / 'parse_results.pkl'),
        restore_analysis_results='latest'
    )
