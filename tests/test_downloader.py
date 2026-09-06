"""
Unit tests for Downloader module
"""

import pytest
import os
import json
import requests
from unittest.mock import Mock, patch, MagicMock

from src.downloader import (
    CNINFODownloader,
    SeleniumDownloader,
    PlaywrightDownloader,
    detect_exchange,
    _build_se_date,
    _normalize_browser_cookies,
    _download_with_browser_cookies,
)
from src.utils import normalize_company_code


@pytest.fixture
def config():
    """Test configuration"""
    return {
        'downloader': {
            'base_url': 'http://www.cninfo.com.cn',
            'api_endpoint': '/new/hisAnnouncement/query',
            'download_path': 'tests/data/downloads',
            'concurrent_downloads': 2,
            'rate_limit': 0.1,
            'retry_attempts': 2,
            'timeout': 10,
            'user_agent': 'Mozilla/5.0',
            'org_id_cache_path': 'tests/data/test_org_map.json',
            'report_types': {
                'annual': '年度报告',
                'semi_annual': '半年度报告'
            },
            'categories': {
                'annual': 'category_ndbg_szsh',
                'semi_annual': 'category_bndbg_szsh'
            }
        }
    }


@pytest.fixture
def mock_org_id_map(tmp_path):
    """Create a mock orgId cache file"""
    cache = {
        '000001': 'gssz0000001',
        '600000': 'gssh0600000',
        '000002': 'gssz0000002',
    }
    cache_path = tmp_path / 'stock_org_map.json'
    with open(cache_path, 'w') as f:
        json.dump(cache, f)
    return str(cache_path)


@pytest.fixture
def downloader(config, tmp_path, mock_org_id_map):
    """Create downloader instance with temp directory"""
    config['downloader']['download_path'] = str(tmp_path / 'downloads')
    config['downloader']['org_id_cache_path'] = mock_org_id_map
    return CNINFODownloader(config)


# ==========================================================
# detect_exchange tests
# ==========================================================

def test_detect_exchange_shenzhen():
    """0, 2, 3 prefix → szse"""
    assert detect_exchange('000001') == 'szse'
    assert detect_exchange('300123') == 'szse'
    assert detect_exchange('200001') == 'szse'


def test_detect_exchange_shanghai():
    """6, 9 prefix → sse"""
    assert detect_exchange('600000') == 'sse'
    assert detect_exchange('601398') == 'sse'
    assert detect_exchange('900001') == 'sse'


def test_detect_exchange_with_prefix():
    """Should handle non-numeric prefixes"""
    assert detect_exchange('SH600000') == 'sse'
    assert detect_exchange('000001.SZ') == 'szse'


# ==========================================================
# _build_se_date tests
# ==========================================================

def test_build_se_date_annual():
    """Annual report date range should span Y-10-01 to Y+1-06-30"""
    result = _build_se_date(2023, 'annual')
    assert result == '2023-10-01~2024-06-30'


def test_build_se_date_semi_annual():
    result = _build_se_date(2023, 'semi_annual')
    assert result == '2023-07-01~2024-01-31'


def test_build_se_date_quarterly():
    result = _build_se_date(2023, 'quarterly')
    assert result == '2023-01-01~2024-06-30'


# ==========================================================
# Downloader initialization
# ==========================================================

def test_downloader_initialization(downloader):
    """Test downloader initialization"""
    assert downloader.base_url == 'http://www.cninfo.com.cn'
    assert downloader.concurrent_downloads == 2
    assert downloader.rate_limit == 0.1
    assert os.path.exists(downloader.download_path)


def test_downloader_loads_org_id_cache(downloader):
    """Test that orgId cache is loaded on init"""
    assert len(downloader._org_id_map) == 3
    assert downloader._org_id_map['000001'] == 'gssz0000001'
    assert downloader._org_id_map['600000'] == 'gssh0600000'


def test_get_org_id_found(downloader):
    """Test orgId lookup for cached stock"""
    assert downloader._get_org_id('000001') == 'gssz0000001'


def test_get_org_id_not_found_triggers_refresh(downloader):
    """Test that missing orgId triggers a refresh attempt"""
    with patch.object(downloader, '_refresh_org_id_map') as mock_refresh:
        downloader._get_org_id('999999')
        mock_refresh.assert_called_once()


# ==========================================================
# normalize_company_code tests
# ==========================================================

def test_normalize_company_code():
    """Test company code normalization"""
    assert normalize_company_code('1') == '000001'
    assert normalize_company_code('600000') == '600000'
    assert normalize_company_code('SH600000') == '600000'
    assert normalize_company_code('000001.SZ') == '000001'


# ==========================================================
# query_announcements tests
# ==========================================================

@patch.object(CNINFODownloader, '_load_org_id_map')
def test_query_announcements_params(mock_load, config, tmp_path):
    """Test that query_announcements sends correct parameters"""
    config['downloader']['download_path'] = str(tmp_path)
    config['downloader']['org_id_cache_path'] = str(tmp_path / 'cache.json')

    dl = CNINFODownloader.__new__(CNINFODownloader)
    dl.config = config['downloader']
    dl.base_url = config['downloader']['base_url']
    dl.api_endpoint = config['downloader']['api_endpoint']
    dl.download_path = str(tmp_path)
    dl.concurrent_downloads = 2
    dl.rate_limit = 0.1
    dl.retry_attempts = 2
    dl.timeout = 10
    dl.cookies = None
    dl.org_id_cache_path = str(tmp_path / 'cache.json')
    dl._org_id_map = {'000001': 'gssz0000001'}
    dl.stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}

    import requests
    dl.session = requests.Session()
    dl.session.headers.update({'User-Agent': 'test'})

    mock_response = Mock()
    mock_response.json.return_value = {
        'announcements': [
            {
                'announcementTitle': '2020年年度报告',
                'adjunctUrl': 'finalpage/2021-04-30/1234567.PDF',
                'announcementTime': '2021-04-30'
            }
        ],
        'totalAnnouncement': 1
    }
    mock_response.raise_for_status = Mock()

    with patch.object(dl.session, 'post', return_value=mock_response) as mock_post:
        results = dl.query_announcements('000001', 2020, 'annual')

        # Verify post was called
        assert mock_post.called
        call_args = mock_post.call_args

        # Verify key parameters
        post_data = call_args.kwargs.get('data') or call_args[1].get('data')
        assert post_data['stock'] == '000001,gssz0000001'  # orgId appended
        assert post_data['column'] == 'szse'  # Shenzhen detected
        assert post_data['isHLtitle'] == 'true'

        # Verify results filtered correctly
        assert len(results) == 1
        assert '年度报告' in results[0]['announcementTitle']


@patch.object(CNINFODownloader, '_load_org_id_map')
def test_query_announcements_shanghai(mock_load, config, tmp_path):
    """Test that Shanghai stocks use column='sse'"""
    config['downloader']['download_path'] = str(tmp_path)
    config['downloader']['org_id_cache_path'] = str(tmp_path / 'cache.json')

    dl = CNINFODownloader.__new__(CNINFODownloader)
    dl.config = config['downloader']
    dl.base_url = config['downloader']['base_url']
    dl.api_endpoint = config['downloader']['api_endpoint']
    dl.download_path = str(tmp_path)
    dl.concurrent_downloads = 2
    dl.rate_limit = 0.1
    dl.retry_attempts = 2
    dl.timeout = 10
    dl.cookies = None
    dl.org_id_cache_path = str(tmp_path / 'cache.json')
    dl._org_id_map = {'600000': 'gssh0600000'}
    dl.stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}

    import requests
    dl.session = requests.Session()
    dl.session.headers.update({'User-Agent': 'test'})

    mock_response = Mock()
    mock_response.json.return_value = {
        'announcements': [
            {
                'announcementTitle': '2020年年度报告',
                'adjunctUrl': 'finalpage/2021-04-30/9999.PDF',
                'announcementTime': '2021-04-30'
            }
        ],
        'totalAnnouncement': 1
    }
    mock_response.raise_for_status = Mock()

    with patch.object(dl.session, 'post', return_value=mock_response) as mock_post:
        dl.query_announcements('600000', 2020, 'annual')

        call_args = mock_post.call_args
        post_data = call_args.kwargs.get('data') or call_args[1].get('data')
        assert post_data['column'] == 'sse'  # Shanghai detected


# ==========================================================
# build_download_url tests
# ==========================================================

def test_build_download_url_https(downloader):
    """Test that download URL uses HTTPS"""
    announcement = {
        'adjunctUrl': 'finalpage/2021-04-30/1234567.PDF',
        'announcementTitle': '2020年年度报告'
    }

    url, filename = downloader.build_download_url(announcement)

    assert url == 'https://static.cninfo.com.cn/finalpage/2021-04-30/1234567.PDF'
    assert filename.endswith('.pdf')
    assert '年度报告' in filename


def test_build_download_url_missing_adjunct(downloader):
    """Test handling missing adjunctUrl"""
    announcement = {
        'announcementTitle': '2020年年度报告'
    }

    url, filename = downloader.build_download_url(announcement)

    assert url is None
    assert filename is None


# ==========================================================
# _filter_announcements tests
# ==========================================================

def test_filter_announcements_basic(downloader):
    """Test basic announcement filtering"""
    announcements = [
        {'announcementTitle': '2020年年度报告'},
        {'announcementTitle': '2020年年度报告摘要'},  # should be filtered out
        {'announcementTitle': '2019年年度报告'},        # wrong year
        {'announcementTitle': '2020年半年度报告'},      # wrong type
    ]

    filtered = downloader._filter_announcements(announcements, 2020, 'annual')

    assert len(filtered) == 1
    assert filtered[0]['announcementTitle'] == '2020年年度报告'


def test_filter_announcements_skips_corrections(downloader):
    """Test that corrections and supplements are filtered"""
    announcements = [
        {'announcementTitle': '2020年年度报告'},
        {'announcementTitle': '2020年年度报告（更正）'},
        {'announcementTitle': '2020年年度报告（补充）'},
        {'announcementTitle': '2020年年度报告（英文版）'},
    ]

    filtered = downloader._filter_announcements(announcements, 2020, 'annual')

    assert len(filtered) == 1


# ==========================================================
# Download statistics tests
# ==========================================================

def test_download_statistics(downloader):
    """Test download statistics tracking"""
    assert downloader.stats['total'] == 0
    assert downloader.stats['success'] == 0
    assert downloader.stats['failed'] == 0
    assert downloader.stats['skipped'] == 0


def test_download_path_creation(downloader, tmp_path):
    """Test download path structure creation"""
    stock_code = '000001'
    year = 2020

    company_dir = os.path.join(downloader.download_path, stock_code, str(year))
    os.makedirs(company_dir, exist_ok=True)

    assert os.path.exists(company_dir)


# ==========================================================
# Session tests
# ==========================================================

def test_session_headers(downloader):
    """Test session headers include required fields"""
    headers = downloader.session.headers
    assert 'User-Agent' in headers
    assert 'Content-Type' in headers
    assert 'Referer' in headers
    assert 'Origin' in headers


def test_cookies_configuration(config, tmp_path, mock_org_id_map):
    """Test cookies configuration"""
    config['downloader']['download_path'] = str(tmp_path / 'dl')
    config['downloader']['org_id_cache_path'] = mock_org_id_map
    cookies = {'JSESSIONID': 'test_session_id'}
    downloader = CNINFODownloader(config, cookies=cookies)

    assert downloader.cookies == cookies


# ==========================================================
# Method existence tests (for new methods)
# ==========================================================

def test_has_download_one_sync(downloader):
    """Test download_one_sync method exists"""
    assert hasattr(downloader, 'download_one_sync')
    assert callable(downloader.download_one_sync)


def test_has_download_one_async(downloader):
    """Test _download_one_async method exists"""
    assert hasattr(downloader, '_download_one_async')
    assert callable(downloader._download_one_async)


def test_normalize_browser_cookies_from_list():
    """Browser cookie exports should normalize into a plain dict."""
    cookies = _normalize_browser_cookies([
        {'name': 'session', 'value': 'abc'},
        {'name': 'token', 'value': 'xyz'},
    ])

    assert cookies == {'session': 'abc', 'token': 'xyz'}


def test_download_with_browser_cookies(tmp_path):
    """Browser-assisted downloads should delegate to requests with cookies."""
    target = tmp_path / 'browser_download.pdf'
    response = Mock()
    response.iter_content.return_value = [b'%PDF-1.4', b' content']
    response.raise_for_status = Mock()

    with patch('src.downloader.requests.get', return_value=response) as mock_get, \
            patch('src.downloader.is_valid_report', return_value=True):
        ok = _download_with_browser_cookies(
            'https://example.com/report.pdf',
            str(target),
            cookies={'session': 'abc'},
            timeout=5
        )

    assert ok is True
    assert target.exists()
    assert mock_get.called


def test_selenium_download_with_login_returns_cookies():
    """Selenium downloader should open the page, optionally fill credentials, and export cookies."""
    downloader = SeleniumDownloader.__new__(SeleniumDownloader)
    downloader.wait_for_captcha = True
    downloader.open_url = Mock()
    downloader._maybe_fill_login_form = Mock()
    downloader.export_cookies = Mock(return_value={'session': 'abc'})
    downloader.driver = None

    with patch('src.downloader.time.sleep') as mock_sleep:
        cookies = SeleniumDownloader.download_with_login(
            downloader,
            username='user',
            password='pass',
            login_url='https://example.com/login',
            wait_seconds=1
        )

    downloader.open_url.assert_called_once_with('https://example.com/login')
    downloader._maybe_fill_login_form.assert_called_once_with('user', 'pass')
    mock_sleep.assert_called_once_with(1)
    assert cookies == {'session': 'abc'}


def test_playwright_download_with_login_returns_cookies():
    """Playwright downloader should expose the same cookie bootstrap flow."""
    downloader = PlaywrightDownloader.__new__(PlaywrightDownloader)
    downloader.open_url = Mock()
    downloader._maybe_fill_login_form = Mock()
    downloader.export_cookies = Mock(return_value={'session': 'xyz'})
    downloader.context = None
    downloader.browser = None
    downloader.playwright = None

    with patch('src.downloader.time.sleep') as mock_sleep:
        cookies = PlaywrightDownloader.download_with_login(
            downloader,
            username='user',
            password='pass',
            login_url='https://example.com/login',
            wait_seconds=1
        )

    downloader.open_url.assert_called_once_with('https://example.com/login')
    downloader._maybe_fill_login_form.assert_called_once_with('user', 'pass')
    mock_sleep.assert_called_once_with(1)
    assert cookies == {'session': 'xyz'}


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


# ==========================================================
# query retry on transient CNINFO errors (502 etc.)
# ==========================================================

def _bare_downloader(config, tmp_path, retry_attempts=3):
    dl = CNINFODownloader.__new__(CNINFODownloader)
    dl.config = config['downloader']
    dl.base_url = config['downloader']['base_url']
    dl.api_endpoint = config['downloader']['api_endpoint']
    dl.download_path = str(tmp_path)
    dl.concurrent_downloads = 2
    dl.rate_limit = 0.1
    dl.retry_attempts = retry_attempts
    dl.timeout = 10
    dl.cookies = None
    dl.org_id_cache_path = str(tmp_path / 'cache.json')
    dl._org_id_map = {'000001': 'gssz0000001'}
    dl.stats = {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}

    import requests
    dl.session = requests.Session()
    dl.session.headers.update({'User-Agent': 'test'})
    return dl


def _query_response(announcements=1):
    mock = Mock()
    rows = [
        {
            'announcementTitle': f'2020年年度报告{i}',
            'adjunctUrl': f'finalpage/2021-04-30/{i}.PDF',
            'announcementTime': '2021-04-30'
        }
        for i in range(announcements)
    ]
    mock.json.return_value = {'announcements': rows, 'totalAnnouncement': announcements}
    mock.raise_for_status = Mock()
    return mock


@patch.object(CNINFODownloader, '_load_org_id_map')
def test_query_retries_on_connection_error_then_succeeds(
        mock_load, config, tmp_path):
    dl = _bare_downloader(config, tmp_path, retry_attempts=3)

    responses = [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom again"),
        _query_response(1),
    ]

    with patch.object(dl.session, 'post', side_effect=responses) as mock_post, \
         patch('src.downloader.time.sleep') as mock_sleep:
        results = dl.query_announcements('000001', 2020, 'annual')

    assert mock_post.call_count == 3
    assert mock_sleep.call_count == 2  # backoff between attempts
    assert len(results) == 1
    assert '年度报告' in results[0]['announcementTitle']


@patch.object(CNINFODownloader, '_load_org_id_map')
def test_query_retries_on_5xx_then_succeeds(mock_load, config, tmp_path):
    import requests
    dl = _bare_downloader(config, tmp_path, retry_attempts=3)

    err = requests.exceptions.HTTPError()
    err.response = Mock(status_code=502)
    responses = [err, err, _query_response(1)]

    with patch.object(dl.session, 'post', side_effect=responses) as mock_post, \
         patch('src.downloader.time.sleep'):
        results = dl.query_announcements('000001', 2020, 'annual')

    assert mock_post.call_count == 3
    assert len(results) == 1


@patch.object(CNINFODownloader, '_load_org_id_map')
def test_query_does_not_retry_on_4xx(mock_load, config, tmp_path):
    import requests
    dl = _bare_downloader(config, tmp_path, retry_attempts=3)

    err = requests.exceptions.HTTPError()
    err.response = Mock(status_code=400)
    responses = [err]

    with patch.object(dl.session, 'post', side_effect=responses) as mock_post, \
         patch('src.downloader.time.sleep'):
        # The pagination loop catches the raise and breaks with no results.
        results = dl.query_announcements('000001', 2020, 'annual')

    assert mock_post.call_count == 1
    assert results == []


@patch.object(CNINFODownloader, '_load_org_id_map')
def test_query_gives_up_after_all_retries(mock_load, config, tmp_path):
    dl = _bare_downloader(config, tmp_path, retry_attempts=3)

    with patch.object(dl.session, 'post',
                      side_effect=requests.exceptions.ConnectionError("down")), \
         patch('src.downloader.time.sleep') as mock_sleep:
        results = dl.query_announcements('000001', 2020, 'annual')

    assert mock_sleep.call_count == 2
    assert results == []
