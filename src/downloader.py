"""
CNINFO Downloader Module
Downloads annual reports and announcements from cninfo.com.cn

Reference: https://github.com/jingmian/cninfo_spider (MIT License)
Reference: https://github.com/legeling/Annualreport_tools (MIT License)
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import aiofiles
import aiohttp
import requests
import urllib3
from loguru import logger
from tqdm import tqdm

from .utils import (
    normalize_company_code,
    sanitize_filename,
    is_valid_report,
    format_bytes
)

# Disable insecure request warnings since CNINFO API has certificate issues
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def detect_exchange(stock_code: str) -> str:
    """
    Detect exchange based on stock code prefix.

    Returns:
        'szse' for Shenzhen, 'sse' for Shanghai
    """
    code = normalize_company_code(stock_code)
    if code.startswith(('6', '9')):
        return 'sse'
    # 0, 3, 2 开头 → 深交所
    return 'szse'


def _build_se_date(year: int, report_type: str) -> str:
    """
    Build a *wide* seDate range that is likely to contain the target report.

    Annual reports for year Y are typically published between Y+1-01 and Y+1-06.
    Semi-annual reports for Y are published roughly Y-07 to Y-12.
    Quarterly reports may span a wider range.

    We intentionally query wide and rely on title / isHLtitle filtering afterwards.
    """
    if report_type == 'annual':
        # 年报: 报告期 Y 年，发布期大约 Y+1 年 1–6 月，查宽到 Y 年 10 月起
        return f'{year}-10-01~{year + 1}-06-30'
    elif report_type == 'semi_annual':
        return f'{year}-07-01~{year + 1}-01-31'
    elif report_type == 'quarterly':
        return f'{year}-01-01~{year + 1}-06-30'
    else:
        # 通用宽查
        return f'{year}-01-01~{year + 1}-12-31'


# ---------------------------------------------------------------------------
# Common HTTP headers for CNINFO
# ---------------------------------------------------------------------------

_CNINFO_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'Origin': 'http://www.cninfo.com.cn',
    'Referer': 'http://www.cninfo.com.cn/new/disclosure',
    'X-Requested-With': 'XMLHttpRequest',
}


def _normalize_browser_cookies(raw_cookies) -> Dict[str, str]:
    """
    Normalize cookie containers from Selenium/Playwright into a plain dict.
    """
    if isinstance(raw_cookies, dict):
        return {str(key): str(value) for key, value in raw_cookies.items()}

    cookies: Dict[str, str] = {}
    if not raw_cookies:
        return cookies

    for item in raw_cookies:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        value = item.get('value')
        if name:
            cookies[str(name)] = '' if value is None else str(value)
    return cookies


def _download_with_browser_cookies(url: str,
                                   save_path: str,
                                   cookies: Optional[Dict[str, str]] = None,
                                   timeout: int = 30) -> bool:
    """
    Download a file using requests and a browser-exported cookie jar.
    """
    response = requests.get(
        url,
        stream=True,
        timeout=timeout,
        cookies=cookies or {},
        headers={'User-Agent': _CNINFO_HEADERS['User-Agent']},
        verify=False
    )
    response.raise_for_status()

    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, 'wb') as handle:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                handle.write(chunk)
    return is_valid_report(save_path)


# ---------------------------------------------------------------------------
# Main downloader
# ---------------------------------------------------------------------------

class CNINFODownloader:
    """
    Downloader for CNINFO reports with concurrent downloads,
    rate limiting, retry logic, and resume capability
    """

    def __init__(self, config: Dict, cookies: Optional[Dict] = None):
        """
        Initialize downloader

        Args:
            config: Configuration dictionary
            cookies: Optional cookies for authenticated requests
        """
        self.config = config['downloader']
        self.base_url = self.config['base_url']
        self.api_endpoint = self.config['api_endpoint']
        self.download_path = self.config['download_path']
        self.concurrent_downloads = self.config.get('concurrent_downloads', 5)
        self.rate_limit = self.config.get('rate_limit', 2.0)
        self.retry_attempts = self.config.get('retry_attempts', 3)
        self.timeout = self.config.get('timeout', 30)
        self.cookies = cookies

        # orgId cache path
        self.org_id_cache_path = self.config.get(
            'org_id_cache_path',
            'data/dictionaries/stock_org_map.json'
        )

        # Create download directory
        Path(self.download_path).mkdir(parents=True, exist_ok=True)

        # Session for synchronous requests
        self.session = requests.Session()
        self.session.headers.update(_CNINFO_HEADERS)
        if self.cookies:
            self.session.cookies.update(self.cookies)

        # orgId mapping: code -> orgId
        self._org_id_map: Dict[str, str] = {}
        self._load_org_id_map()

        # Track download statistics
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'skipped': 0
        }

        logger.info(f"CNINFO Downloader initialized: {self.concurrent_downloads} concurrent, "
                    f"{len(self._org_id_map)} stocks in orgId cache")

    # ------------------------------------------------------------------
    # orgId management
    # ------------------------------------------------------------------

    def _load_org_id_map(self) -> None:
        """Load orgId mapping from local cache, or fetch from CNINFO if missing."""
        cache_path = Path(self.org_id_cache_path)

        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    self._org_id_map = json.load(f)
                logger.info(f"Loaded orgId cache: {len(self._org_id_map)} entries")
                return
            except Exception as e:
                logger.warning(f"Failed to load orgId cache: {e}")

        # Fetch from CNINFO
        self._refresh_org_id_map()

    def _refresh_org_id_map(self) -> None:
        """Fetch full stock list from CNINFO and rebuild the orgId cache."""
        url = 'http://www.cninfo.com.cn/new/data/szse_stock.json'
        logger.info("Fetching stock list from CNINFO for orgId mapping...")

        try:
            # CNINFO API has SSL cert issues, disable verify
            resp = self.session.get(url, timeout=self.timeout, verify=False)
            resp.raise_for_status()
            data = resp.json()

            stock_list = data.get('stockList', [])
            for item in stock_list:
                code = str(item.get('code', '')).strip()
                org_id = str(item.get('orgId', '')).strip()
                if code and org_id:
                    self._org_id_map[code] = org_id

            # Persist cache
            cache_path = Path(self.org_id_cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(self._org_id_map, f, ensure_ascii=False)

            logger.info(f"Refreshed orgId cache: {len(self._org_id_map)} entries")

        except Exception as e:
            logger.error(f"Failed to fetch orgId mapping: {e}")

    def _get_org_id(self, stock_code: str) -> str:
        """
        Get orgId for a stock code, with on-demand refresh if not found.
        """
        code = normalize_company_code(stock_code)
        org_id = self._org_id_map.get(code)

        if not org_id:
            logger.warning(f"orgId not found for {code}, refreshing cache...")
            self._refresh_org_id_map()
            org_id = self._org_id_map.get(code, '')

        return org_id

    # ------------------------------------------------------------------
    # Announcement query
    # ------------------------------------------------------------------

    def _post_query_with_retry(self, params: Dict) -> Optional[Dict]:
        """POST an announcement query, retrying transient failures.

        CNINFO's query endpoint intermittently returns 502/503 or drops
        connections; a bare failure used to silently drop that year's rows
        (the loop below just breaks). Connection errors, timeouts and 5xx
        are retried with exponential backoff; 4xx fails immediately.
        Returns parsed JSON, or None when every attempt failed.
        """
        url_query = self.base_url + self.api_endpoint
        last_exc: Optional[Exception] = None

        for attempt in range(self.retry_attempts):
            try:
                response = self.session.post(
                    url_query, data=params, timeout=self.timeout, verify=False
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                status = getattr(e.response, 'status_code', 0)
                if status < 500:
                    raise  # client error: retrying won't help
                last_exc = e
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                last_exc = e

            if attempt < self.retry_attempts - 1:
                wait = 2 ** attempt
                logger.warning(
                    f"CNINFO query transient error "
                    f"(attempt {attempt + 1}/{self.retry_attempts}), "
                    f"retrying in {wait}s: {last_exc}"
                )
                time.sleep(wait)

        logger.error(f"CNINFO query failed after "
                     f"{self.retry_attempts} attempts: {last_exc}")
        return None

    def query_announcements(self,
                            stock_code: str,
                            year: int,
                            report_type: str = 'annual') -> List[Dict]:
        """
        Query announcement list from CNINFO API.

        Uses wide date range + title filtering to avoid missing reports.

        Args:
            stock_code: 6-digit stock code
            year: Report year (the fiscal year of the report)
            report_type: Type of report (annual, semi_annual, quarterly)

        Returns:
            List of announcement dictionaries
        """
        stock_code = normalize_company_code(stock_code)
        org_id = self._get_org_id(stock_code)
        exchange = detect_exchange(stock_code)

        # Map report type to category
        categories = self.config.get('categories', {})
        category = categories.get(report_type, 'category_ndbg_szsh')

        # Build stock parameter: "代码,orgId"
        stock_param = f'{stock_code},{org_id}' if org_id else stock_code

        # Wide date range
        se_date = _build_se_date(year, report_type)

        # Paginated collection
        all_announcements: List[Dict] = []
        page_num = 1
        max_pages = 10  # safety limit

        while page_num <= max_pages:
            params = {
                'pageNum': page_num,
                'pageSize': 30,
                'column': exchange,
                'tabName': 'fulltext',
                'stock': stock_param,
                'searchkey': '',
                'secid': '',
                'plate': '',
                'category': category,
                'trade': '',
                'seDate': se_date,
                'isHLtitle': 'true',
            }

            try:
                data = self._post_query_with_retry(params)
                if data is None:
                    break

                announcements = data.get('announcements', []) or []
                if not announcements:
                    break

                all_announcements.extend(announcements)

                # Check if there are more pages
                total_count = data.get('totalAnnouncement', 0)
                if len(all_announcements) >= total_count:
                    break

                page_num += 1
                time.sleep(self.rate_limit)

            except Exception as e:
                logger.error(f"Failed to query announcements for {stock_code} "
                             f"(page {page_num}): {e}")
                break

        # Post-filter: keep only relevant titles for the target year
        filtered = self._filter_announcements(all_announcements, year, report_type)

        logger.info(f"Found {len(filtered)} announcements for {stock_code} "
                    f"({year} {report_type}) [raw: {len(all_announcements)}]")
        return filtered

    def _filter_announcements(self,
                              announcements: List[Dict],
                              year: int,
                              report_type: str) -> List[Dict]:
        """
        Post-filter announcements by title to match the target year & type.

        For annual reports, we keep announcements whose title contains
        the target year string (e.g., "2023" for 2023 年报).
        """
        year_str = str(year)

        # Report type keywords for title matching
        type_keywords = {
            'annual': ['年度报告', '年报'],
            'semi_annual': ['半年度报告', '半年报', '中期报告'],
            'quarterly': ['季度报告', '季报'],
        }
        keywords = type_keywords.get(report_type, [])

        filtered = []
        for ann in announcements:
            title = ann.get('announcementTitle', '')

            # Must contain the target year
            if year_str not in title:
                continue

            # Must match at least one report type keyword
            if keywords and not any(kw in title for kw in keywords):
                continue

            # Specifically prevent "半年度报告" from matching when we just want "年度报告"
            if report_type == 'annual' and '半' in title:
                continue

            # Skip supplements, corrections, and summaries (摘要)
            skip_words = ['摘要', '更正', '补充', '英文', 'H股']
            if any(sw in title for sw in skip_words):
                continue

            filtered.append(ann)

        return filtered

    # ------------------------------------------------------------------
    # Download URL construction
    # ------------------------------------------------------------------

    def build_download_url(self, announcement: Dict) -> Tuple[str, str]:
        """
        Build download URL from announcement data

        Args:
            announcement: Announcement dictionary

        Returns:
            Tuple of (download_url, filename)
        """
        adjunct_url = announcement.get('adjunctUrl', '')

        if not adjunct_url:
            return None, None

        # CNINFO has migrated to HTTPS
        download_url = f"https://static.cninfo.com.cn/{adjunct_url}"

        # Extract filename
        filename = announcement.get('announcementTitle', 'report')
        filename = sanitize_filename(filename) + '.pdf'

        return download_url, filename

    # ------------------------------------------------------------------
    # Core download function (shared by batch & streaming modes)
    # ------------------------------------------------------------------

    async def _download_one_async(self,
                                  url: str,
                                  save_path: str,
                                  session: aiohttp.ClientSession,
                                  semaphore: Optional[asyncio.Semaphore] = None
                                  ) -> bool:
        """
        Download a single file asynchronously with retry logic.

        This is the shared core used by both batch_download_async and
        download_one_sync.

        Args:
            url: Download URL
            save_path: Path to save file
            session: aiohttp session
            semaphore: Optional semaphore for concurrency limiting

        Returns:
            True if successful, False otherwise
        """
        # Check if file already exists and is valid (resume capability)
        if os.path.exists(save_path) and is_valid_report(save_path):
            logger.debug(f"File already exists: {save_path}")
            self.stats['skipped'] += 1
            return True

        # Ensure parent directory exists
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        async def _do_download() -> bool:
            for attempt in range(self.retry_attempts):
                try:
                    async with session.get(url, timeout=self.timeout, ssl=False) as response:
                        response.raise_for_status()

                        # Download in chunks
                        async with aiofiles.open(save_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(8192):
                                await f.write(chunk)

                        # Validate downloaded file
                        if is_valid_report(save_path):
                            self.stats['success'] += 1
                            size = os.path.getsize(save_path)
                            logger.debug(f"Downloaded: {save_path} ({format_bytes(size)})")

                            # Rate limiting
                            await asyncio.sleep(self.rate_limit)
                            return True
                        else:
                            os.remove(save_path)
                            raise ValueError("Invalid PDF file")

                except Exception as e:
                    logger.warning(f"Attempt {attempt + 1}/{self.retry_attempts} "
                                   f"failed for {url}: {e}")
                    if attempt < self.retry_attempts - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    else:
                        self.stats['failed'] += 1
                        logger.error(f"Failed to download: {url}")
                        return False
            return False

        if semaphore is not None:
            async with semaphore:
                return await _do_download()
        else:
            return await _do_download()

    def download_one_sync(self, url: str, save_path: str) -> bool:
        """
        Synchronous wrapper around _download_one_async for streaming mode.

        Creates a temporary aiohttp session and event loop.

        Args:
            url: Download URL
            save_path: Path to save file

        Returns:
            True if successful, False otherwise
        """
        async def _run() -> bool:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=dict(self.session.headers),
                cookies=self.cookies
            ) as aio_session:
                return await self._download_one_async(url, save_path, aio_session)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If called from within an async context, create a new loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(lambda: asyncio.run(_run())).result()
            else:
                return asyncio.run(_run())
        except RuntimeError:
            return asyncio.run(_run())

    # ------------------------------------------------------------------
    # Batch download (original mode, preserved)
    # ------------------------------------------------------------------

    async def batch_download_async(self, download_tasks: List[Tuple[str, str]]) -> None:
        """
        Batch download files asynchronously

        Args:
            download_tasks: List of (url, save_path) tuples
        """
        semaphore = asyncio.Semaphore(self.concurrent_downloads)

        # Configure aiohttp session
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        connector = aiohttp.TCPConnector(limit=self.concurrent_downloads, ssl=False)

        async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=dict(self.session.headers),
                cookies=self.cookies
        ) as session:
            tasks = [
                self._download_one_async(url, save_path, session, semaphore)
                for url, save_path in download_tasks
            ]

            # Use tqdm for progress bar
            with tqdm(total=len(tasks), desc="Downloading") as pbar:
                for coro in asyncio.as_completed(tasks):
                    await coro
                    pbar.update(1)

    def download_reports(self,
                         company_codes: List[str],
                         years: List[int],
                         report_types: List[str] = None) -> Dict[Tuple[str, int, str], List[Dict]]:
        """
        Download reports for multiple companies and years

        Args:
            company_codes: List of stock codes
            years: List of years
            report_types: List of report types (default: ['annual'])

        Returns:
            Dictionary mapping (stock_code, year, report_type) to list of metadata entries
        """
        if report_types is None:
            report_types = ['annual']

        # Collect all download tasks
        download_tasks = []
        metadata: Dict[Tuple[str, int, str], List[Dict]] = {}

        logger.info(f"Querying reports for {len(company_codes)} companies, "
                    f"{len(years)} years, {len(report_types)} types")

        for stock_code in company_codes:
            for year in years:
                for report_type in report_types:
                    # Query announcements
                    announcements = self.query_announcements(stock_code, year, report_type)

                    for announcement in announcements:
                        url, filename = self.build_download_url(announcement)

                        if not url:
                            continue

                        # Construct save path
                        company_dir = os.path.join(
                            self.download_path,
                            stock_code,
                            str(year)
                        )
                        os.makedirs(company_dir, exist_ok=True)

                        save_path = os.path.join(company_dir, filename)
                        download_tasks.append((url, save_path))

                        # Store metadata
                        key = (stock_code, year, report_type)
                        entry = {
                            'file_path': save_path,
                            'url': url,
                            'title': announcement.get('announcementTitle', ''),
                            'date': announcement.get('announcementTime', '')
                        }
                        metadata.setdefault(key, []).append(entry)

        if not download_tasks:
            logger.warning("No reports found to download")
            return metadata

        # Reset statistics
        self.stats = {'total': len(download_tasks), 'success': 0, 'failed': 0, 'skipped': 0}

        # Run async downloads
        logger.info(f"Starting download of {len(download_tasks)} files")
        asyncio.run(self.batch_download_async(download_tasks))

        # Log statistics
        logger.info(f"Download complete: {self.stats['success']} success, "
                    f"{self.stats['failed']} failed, {self.stats['skipped']} skipped")

        return metadata


class SeleniumDownloader:
    """
    Alternative downloader using Selenium for dynamic content
    Use when CNINFO requires CAPTCHA or JavaScript rendering
    """

    def __init__(self, headless: bool = True, wait_for_manual_captcha: bool = False):
        """
        Initialize Selenium downloader

        Args:
            headless: Run browser in headless mode
            wait_for_manual_captcha: Wait for user to solve CAPTCHA manually
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options

            options = Options()
            if headless:
                options.add_argument('--headless')
            options.add_argument('--disable-gpu')
            options.add_argument('--no-sandbox')

            self.driver = webdriver.Chrome(options=options)
            self.wait_for_captcha = wait_for_manual_captcha

            logger.info("Selenium driver initialized")

        except ImportError:
            logger.error("Selenium not installed. Install with: pip install selenium")
            raise

    def open_url(self, url: str) -> None:
        """Open a URL in the Selenium-controlled browser."""
        self.driver.get(url)

    def export_cookies(self) -> Dict[str, str]:
        """Export the current Selenium cookie jar as a plain dict."""
        return _normalize_browser_cookies(self.driver.get_cookies())

    def download_url(self, url: str, save_path: str, timeout: int = 30) -> bool:
        """Download a URL with the authenticated Selenium cookie jar."""
        return _download_with_browser_cookies(
            url,
            save_path,
            cookies=self.export_cookies(),
            timeout=timeout
        )

    def _maybe_fill_login_form(self, username: str, password: str) -> None:
        """
        Best-effort autofill for common login field selectors.
        """
        try:
            from selenium.webdriver.common.by import By
        except ImportError:
            return

        username_selectors = [
            'input[name="username"]',
            'input[name="userName"]',
            'input[type="email"]',
            '#username',
            '#userName',
        ]
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            '#password',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            '.login-btn',
        ]

        def _find(selectors):
            for selector in selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    return elements[0]
            return None

        username_input = _find(username_selectors)
        password_input = _find(password_selectors)
        if username_input and password_input:
            username_input.clear()
            username_input.send_keys(username)
            password_input.clear()
            password_input.send_keys(password)
            submit_button = _find(submit_selectors)
            if submit_button:
                submit_button.click()

    def download_with_login(self,
                            username: Optional[str] = None,
                            password: Optional[str] = None,
                            login_url: str = 'https://www.cninfo.com.cn/new/disclosure',
                            wait_seconds: int = 30) -> Dict[str, str]:
        """
        Open the CNINFO browser flow and return authenticated cookies.
        """
        self.open_url(login_url)
        if username and password:
            self._maybe_fill_login_form(username, password)

        if self.wait_for_captcha and wait_seconds > 0:
            logger.info(f"Waiting {wait_seconds}s for manual verification in Selenium...")
            time.sleep(wait_seconds)

        cookies = self.export_cookies()
        logger.info(f"Exported {len(cookies)} cookies from Selenium session")
        return cookies

    def close(self) -> None:
        """Close the Selenium browser."""
        if hasattr(self, 'driver') and self.driver:
            self.driver.quit()
            self.driver = None

    def __del__(self):
        """Cleanup driver on deletion"""
        self.close()


# Optional: Playwright implementation
class PlaywrightDownloader:
    """
    Modern alternative using Playwright for better performance.
    """

    def __init__(self, headless: bool = True):
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=headless)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()
            logger.info("Playwright browser initialized")
        except ImportError:
            logger.error("Playwright not installed. Install with: pip install playwright")
            raise

    def open_url(self, url: str) -> None:
        """Open a URL in the Playwright-controlled browser."""
        self.page.goto(url, wait_until='domcontentloaded')

    def export_cookies(self) -> Dict[str, str]:
        """Export current Playwright cookies for requests-based downloads."""
        return _normalize_browser_cookies(self.context.cookies())

    def download_url(self, url: str, save_path: str, timeout: int = 30) -> bool:
        """Download a URL using the authenticated Playwright cookie jar."""
        return _download_with_browser_cookies(
            url,
            save_path,
            cookies=self.export_cookies(),
            timeout=timeout
        )

    def _maybe_fill_login_form(self, username: str, password: str) -> None:
        """
        Best-effort autofill for common login field selectors in Playwright.
        """
        username_selectors = [
            'input[name="username"]',
            'input[name="userName"]',
            'input[type="email"]',
            '#username',
            '#userName',
        ]
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            '#password',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            '.login-btn',
        ]

        def _fill_first(selectors, value: str) -> bool:
            for selector in selectors:
                locator = self.page.locator(selector)
                if locator.count():
                    locator.first.fill(value)
                    return True
            return False

        username_ok = _fill_first(username_selectors, username)
        password_ok = _fill_first(password_selectors, password)

        if username_ok and password_ok:
            for selector in submit_selectors:
                locator = self.page.locator(selector)
                if locator.count():
                    locator.first.click()
                    break

    def download_with_login(self,
                            username: Optional[str] = None,
                            password: Optional[str] = None,
                            login_url: str = 'https://www.cninfo.com.cn/new/disclosure',
                            wait_seconds: int = 30) -> Dict[str, str]:
        """
        Open the CNINFO browser flow in Playwright and return authenticated cookies.
        """
        self.open_url(login_url)
        if username and password:
            self._maybe_fill_login_form(username, password)
        if wait_seconds > 0:
            logger.info(f"Waiting {wait_seconds}s for manual verification in Playwright...")
            time.sleep(wait_seconds)
        cookies = self.export_cookies()
        logger.info(f"Exported {len(cookies)} cookies from Playwright session")
        return cookies

    def close(self) -> None:
        """Close Playwright resources."""
        if hasattr(self, 'context') and self.context:
            self.context.close()
            self.context = None
        if hasattr(self, 'browser') and self.browser:
            self.browser.close()
            self.browser = None
        if hasattr(self, 'playwright') and self.playwright:
            self.playwright.stop()
            self.playwright = None

    def __del__(self):
        self.close()
