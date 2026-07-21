from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
import hashlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
import time
from urllib.parse import urldefrag, urljoin, urlparse, unquote

import threading

import subprocess

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


NOISE_PARENT_TAGS = {
    'nav',
    'header',
    'footer',
    'aside',
    'noscript',
}

NOISE_KEYWORDS = ('footer', 'cookie', 'consent', 'breadcrumb', 'share', 'related')

VIDEO_FILE_RE = re.compile(r'\.(mp4|m3u8|webm|ogg)(\?|$)', re.I)
EMBED_RE = re.compile(r'youtube|youtu\.be|vimeo|player|wistia', re.I)
IMAGE_FILE_RE = re.compile(r'\.(png|jpe?g|webp|gif|bmp|svg)(\?|$)', re.I)
HTML_CONTENT_TYPE_RE = re.compile(r'^\s*(?:text/html|application/xhtml\+xml)\s*(?:;|$)', re.I)
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/138.0.0.0 Safari/537.36'
)


_log_path: Path | None = None

# 每个线程独立的 Playwright + Browser 缓存（Playwright 同步 API 非线程安全）
_thread_local = threading.local()


def _get_thread_browser(cdp_url: str = '', headless: bool = True):
    """获取当前线程的 Playwright browser 实例。每个线程独立一个，避免 greenlet 冲突。"""
    if hasattr(_thread_local, 'browser') and _thread_local.browser is not None:
        try:
            _thread_local.browser.contexts  # 探活
            return _thread_local.browser
        except Exception:
            _thread_local.browser = None

    if not hasattr(_thread_local, 'playwright') or _thread_local.playwright is None:
        _thread_local.playwright = sync_playwright().start()

    if cdp_url:
        _log(f'线程内通过CDP连接Chrome: {cdp_url}')
        _thread_local.browser = _thread_local.playwright.chromium.connect_over_cdp(cdp_url)
    else:
        _log('线程内启动本地 Playwright 浏览器')
        _thread_local.browser = _thread_local.playwright.chromium.launch(headless=headless)
    return _thread_local.browser


def _get_thread_context(cdp_url: str = '', headless: bool = True):
    """获取当前线程共享的 browser context。
    CDP 模式复用浏览器已有 context（browser.contexts[0]），这样 page 是现有窗口中的 tab。
    每个线程通过自己独立的 CDP 连接访问，避免 greenlet 冲突。
    """
    if hasattr(_thread_local, 'context') and _thread_local.context is not None:
        try:
            _thread_local.context.pages  # 探活
            return _thread_local.context
        except Exception:
            _thread_local.context = None

    browser = _get_thread_browser(cdp_url, headless)
    if cdp_url and browser.contexts:
        # 复用浏览器已有的默认 context（= 现有 Chrome 窗口），只开 tab 不开新窗口
        _thread_local.context = browser.contexts[0]
    else:
        _thread_local.context = browser.new_context(
            ignore_https_errors=True, user_agent=DEFAULT_USER_AGENT
        )
    return _thread_local.context


def set_log_file(path: Path):
    """设置日志文件路径。设置后 _log 会同时写入该文件。"""
    global _log_path
    _log_path = Path(path)
    _log_path.parent.mkdir(parents=True, exist_ok=True)


def _log(message: str):
    now = datetime.now().strftime('%H:%M:%S')
    line = f'[{now}] {message}'
    print(line)
    if _log_path is not None:
        try:
            with open(_log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass


def _safe_name_from_url(url: str, max_len: int = 120) -> str:
    p = urlparse(url)
    host = p.netloc.replace(':', '_')
    path = _sanitize_filename_component(unquote(p.path.strip('/').replace('/', '_')) or '')
    raw_query = unquote(re.sub(r'[^a-zA-Z0-9]+', '_', p.query).strip('_'))
    query = _sanitize_filename_component(raw_query) if raw_query else ''
    if path and query:
        name = f"{host}_{path}_{query}"
    elif path:
        name = f"{host}_{path}"
    elif query:
        name = f"{host}_{query}"
    else:
        name = host
    if len(name) > max_len:
        name = name[:max_len].rstrip('_')
    return name


def _path_name_from_url(url: str, max_len: int = 120) -> str:
    p = urlparse(url)
    path = _sanitize_filename_component(unquote(p.path.strip('/').replace('/', '_')) or '')
    raw_query = unquote(re.sub(r'[^a-zA-Z0-9]+', '_', p.query).strip('_'))
    query = _sanitize_filename_component(raw_query) if raw_query else ''
    if path and query:
        name = f"{path}_{query}"
    elif path:
        name = path
    elif query:
        name = query
    else:
        name = 'index'
    if len(name) > max_len:
        name = name[:max_len].rstrip('_')
    return name


def _sanitize_filename_component(value: str) -> str:
    # Replace characters that are invalid in Windows filenames.
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value)
    cleaned = cleaned.strip(' .')
    return cleaned or ''


def _build_output_base_name(url: str, page_index: int, timestamp: str) -> str:
    page_name = _path_name_from_url(url)
    return f"{page_index:04d}_{page_name}_html_{timestamp}"


def _resolve_url(base: str, link: str) -> str:
    if not link:
        return ''
    return urljoin(base, link)


def _normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return ''
    path = parsed.path or '/'
    return parsed._replace(path=path, fragment='').geturl()


def _is_same_domain(url: str, root_host: str) -> bool:
    host = urlparse(url).netloc.lower()
    root = root_host.lower()
    return host == root or host.endswith(f'.{root}')


def _is_in_noise_area(tag: Tag) -> bool:
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in NOISE_PARENT_TAGS:
            return True
        if parent.get('aria-hidden') == 'true':
            return True
        classes = ' '.join(parent.get('class', [])).lower()
        pid = (parent.get('id') or '').lower()
        if any(k in classes for k in NOISE_KEYWORDS):
            return True
        if any(k in pid for k in NOISE_KEYWORDS):
            return True
    return False


def _clean_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()


def _candidate_text_nodes(container: Tag):
    for node in container.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li', 'span', 'figcaption']):
        if _is_in_noise_area(node):
            continue
        text = _clean_text(node.get_text(' ', strip=True))
        if len(text) >= 12:
            yield text


def _nearby_description(tag: Tag) -> str:
    figure = tag.find_parent('figure')
    if figure and not _is_in_noise_area(figure):
        caption = figure.find('figcaption')
        if caption:
            text = _clean_text(caption.get_text(' ', strip=True))
            if text:
                return text

    if tag.has_attr('title'):
        text = _clean_text(tag.get('title', ''))
        if text:
            return text
    if tag.has_attr('aria-label'):
        text = _clean_text(tag.get('aria-label', ''))
        if text:
            return text

    # Prefer closest meaningful text in the same content block.
    content_root = tag.find_parent(['article', 'section', 'main', 'div']) or tag.parent
    if isinstance(content_root, Tag):
        for text in _candidate_text_nodes(content_root):
            if text:
                return text[:240]

    prev_text = tag.find_previous(string=True)
    if isinstance(prev_text, NavigableString):
        text = _clean_text(str(prev_text))
        if len(text) >= 12:
            return text[:240]

    return ''


def _extract_video_items(soup: BeautifulSoup, base_url: str):
    items = []

    for tag in soup.find_all(['video', 'iframe', 'a']):
        if not isinstance(tag, Tag) or _is_in_noise_area(tag):
            continue

        src = ''
        if tag.name == 'video':
            src = tag.get('src', '')
            if not src:
                source = tag.find('source')
                if source:
                    src = source.get('src', '')
        elif tag.name == 'iframe':
            iframe_src = tag.get('src', '')
            if iframe_src and (EMBED_RE.search(iframe_src) or VIDEO_FILE_RE.search(iframe_src)):
                src = iframe_src
        elif tag.name == 'a':
            href = tag.get('href', '')
            if href and VIDEO_FILE_RE.search(href):
                src = href

        src = _resolve_url(base_url, src)
        if not src:
            continue

        description = _nearby_description(tag)
        items.append(
            {
                'type': 'video',
                'original_url': src,
                'tos_url': src,
                'note': description,
                'alt': '',
            }
        )

    # De-duplicate while preserving DOM order.
    seen = set()
    ordered = []
    for item in items:
        key = item['original_url']
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)

    for idx, item in enumerate(ordered, start=1):
        item['index'] = idx

    return ordered


def _media_from_tag(tag: Tag, base_url: str):
    src = ''
    media_type = ''

    if tag.name == 'video':
        src = tag.get('src', '')
        if not src:
            source = tag.find('source')
            if source:
                src = source.get('src', '')
        media_type = 'video'
    elif tag.name == 'iframe':
        iframe_src = tag.get('src', '')
        if iframe_src and (EMBED_RE.search(iframe_src) or VIDEO_FILE_RE.search(iframe_src)):
            src = iframe_src
            media_type = 'video'
    elif tag.name == 'a':
        href = tag.get('href', '')
        if href and VIDEO_FILE_RE.search(href):
            src = href
            media_type = 'video'
        elif href and IMAGE_FILE_RE.search(href):
            src = href
            media_type = 'image'
    elif tag.name == 'img':
        src = tag.get('src', '') or tag.get('data-src', '')
        media_type = 'image'

    src = _resolve_url(base_url, src)
    if not src or not media_type:
        return None

    note = _nearby_description(tag)
    alt = _clean_text(tag.get('alt', '')) if tag.name == 'img' else ''

    return {
        'type': media_type,
        'original_url': src,
        'tos_url': src,
        'note': note,
        'alt': alt,
    }


def extract_content_blocks(html: str, base_url: str):
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.find('main') or soup.body or soup

    blocks = []
    seen_media = set()
    media_index = 0

    for tag in root.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li', 'video', 'iframe', 'a', 'img']):
        if not isinstance(tag, Tag) or _is_in_noise_area(tag):
            continue

        media = _media_from_tag(tag, base_url)
        if media:
            key = media['original_url']
            if key in seen_media:
                continue
            seen_media.add(key)
            media_index += 1
            media['index'] = media_index
            media['id'] = hashlib.md5(f"{base_url}|{key}|{media_index}".encode('utf-8')).hexdigest()
            blocks.append(media)
            continue

        text = _clean_text(tag.get_text(' ', strip=True))
        if len(text) < 2:
            continue
        if blocks and isinstance(blocks[-1], str) and blocks[-1] == text:
            continue
        blocks.append(text)

    return blocks


def _extract_links(html: str, base_url: str, root_host: str):
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        if not href or href.startswith('#'):
            continue
        if href.startswith(('mailto:', 'tel:', 'javascript:')):
            continue
        #文件无扩展名或者扩展名是.html或.htm的链接
        if not re.search(r'\.html?$', urlparse(href).path, re.IGNORECASE) and '.' in Path(urlparse(href).path).suffix:
            continue
        resolved = _normalize_url(_resolve_url(base_url, href))
        if not resolved:
            continue
        if not _is_same_domain(resolved, root_host):
            continue
        links.append(resolved)
    return links


def extract_videos(html: str, base_url: str):
    soup = BeautifulSoup(html, 'html.parser')
    return _extract_video_items(soup, base_url)


def _is_html_document(html: str) -> bool:
    sample = (html or '')[:4000].lower()
    return ('<html' in sample) or ('<!doctype html' in sample)


def _is_challenge_or_block_page(html: str) -> bool:
    sample = (html or '').lower()
    strong_markers = (
        'just a moment',
        'cf-browser-verification',
        'attention required',
        'checking your browser',
        'challenges.cloudflare.com',
        '__cf_chl_',
        'cf-chl-',
        'turnstile',
        'enable javascript and cookies',
        'the request could not be satisfied',
        'request blocked',
        'generated by cloudfront',
        '正在进行安全验证',
        '请验证您是真人',
        '安全服务防护恶意自动程序',
        '403 error: page not available',
        '404 error: page not found',
    )
    # Do not treat generic "cloudflare" mentions as challenge pages;
    # many normal sites include Cloudflare assets and would be false positives.
    return any(m in sample for m in strong_markers)


def _try_click_challenge_checkbox(page) -> bool:
    selectors = [
        "input[type='checkbox']",
        "[role='checkbox']",
        "label:has-text('请验证您是真人')",
        "label:has-text('Verify you are human')",
        "text=请验证您是真人",
        "text=Verify you are human",
    ]

    # Try direct click on main document.
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=250, force=True)
                return True
        except Exception:
            continue

    # Try click inside embedded challenge frames.
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        for selector in selectors:
            try:
                locator = frame.locator(selector).first
                if locator.count() > 0:
                    locator.click(timeout=250, force=True)
                    return True
            except Exception:
                continue

    # Cloudflare challenge is often rendered in an iframe; try clicking iframe itself.
    frame_selectors = [
        "iframe[title*='security challenge']",
        "iframe[title*='Cloudflare']",
        "iframe[src*='challenges.cloudflare.com']",
    ]
    for selector in frame_selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=250, force=True)
                return True
        except Exception:
            continue

    return False


def fetch_html_with_playwright(
    url: str,
    timeout=30,
    wait_seconds: float = 5.0,
    headless: bool = True,
    cdp_url: str = '',
) -> dict:
    """返回 {'html': str, 'content_type': str}"""
    if sync_playwright is None:
        raise RuntimeError('Playwright is not installed. Run: pip install playwright and playwright install chromium')

    body_deadline = time.time() + max(float(timeout), 10.0)

    def _navigate_and_capture(page, url, body_deadline, wait_seconds):
        """在已打开的 page 上导航并捕获 HTML。返回 (html, content_type)。"""
        _log(f'开始打开页面: {url}')
        page.route("**/google-analytics.com/**", lambda route: route.abort())
        response = page.goto(url, wait_until='domcontentloaded', timeout=max(10.0, body_deadline - time.time()) * 1000)
        ctype = response.headers.get('content-type', '') if response is not None else ''

        try:
            _log(f'等待 DOM 加载完成: {url} 超时时间: {max(10.0, body_deadline - time.time()):.1f}秒')
            page.wait_for_load_state('domcontentloaded', timeout=max(10.0, body_deadline - time.time()) * 1000)
        except TimeoutError:
            _log(f'等待 DOM 加载超时: {url}')
            return '', ctype

        current_html = page.content()
        settle_wait_seconds = max(float(wait_seconds), 1.0)

        if _is_challenge_or_block_page(current_html):
            challenge_wait_seconds = max(10.0, settle_wait_seconds)
            challenge_deadline = time.time() + challenge_wait_seconds
            _log(f'检测到挑战页，额外最多等待{challenge_wait_seconds:.1f}秒: {url}')
            while time.time() < challenge_deadline:
                if _try_click_challenge_checkbox(page):
                    _log(f'已尝试自动勾选安全验证: {url}')
                page.wait_for_timeout(1000)
                current_html = page.content()
                if not _is_challenge_or_block_page(current_html):
                    break
        else:
            _log(f'未检测到挑战页，额外等待{settle_wait_seconds/2:.1f}秒: {url}')
            if settle_wait_seconds > 0:
                time.sleep(settle_wait_seconds / 2)

        return page.content(), ctype

    use_cdp = bool(cdp_url)
    if use_cdp:
        # CDP：复用 thread-local browser + context，只开/关 tab
        browser = _get_thread_browser(cdp_url, headless)
        context = _get_thread_context(cdp_url, headless)
        page = context.new_page()
        html = ''
        content_type = ''
        try:
            html, content_type = _navigate_and_capture(page, url, body_deadline, wait_seconds)
            _log(f'HTML已抓取，准备关闭page: {url}, 字节数: {len(html)}')
        except Exception as e:
            _log(f'抓取页面异常: {url}, 错误: {e}')
            raise
        finally:
            page.close()
            _log(f'CDP tab已关闭: {url}')
    else:
        # 非 CDP：每次独立启动/关闭，不缓存
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(ignore_https_errors=True, user_agent=DEFAULT_USER_AGENT)
            page = context.new_page()
            html = ''
            content_type = ''
            try:
                html, content_type = _navigate_and_capture(page, url, body_deadline, wait_seconds)
                _log(f'HTML已抓取，准备关闭page: {url}, 字节数: {len(html)}')
            except Exception as e:
                _log(f'抓取页面异常: {url}, 错误: {e}')
                raise
            finally:
                page.close()
                context.close()
                browser.close()
                _log(f'浏览器已关闭: {url}')
    return {'html': html, 'content_type': content_type}


def fetch_html(
    url: str,
    timeout=60,
    renderer: str = 'auto',
    playwright_headless: bool = True,
    playwright_wait_seconds: float = 5.0,
    playwright_cdp_url: str = '',
) -> dict:
    """返回 {'html': str, 'content_type': str}"""
    headers = {
        'User-Agent': DEFAULT_USER_AGENT
    }
    if renderer == 'playwright':
        result = fetch_html_with_playwright(
            url,
            timeout=max(timeout, 60),
            headless=playwright_headless,
            wait_seconds=playwright_wait_seconds,
            cdp_url=playwright_cdp_url,
        )
        return result

    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        ctype = r.headers.get('content-type', '')
        # Many sites return missing/incorrect charset headers. Prefer apparent encoding
        # so Unicode punctuation like em dash is preserved in saved JSON.
        if not r.encoding or r.encoding.lower() in ('iso-8859-1', 'latin1'):
            r.encoding = r.apparent_encoding or 'utf-8'
        html = r.text
    except requests.RequestException:
        if renderer == 'auto':
            result = fetch_html_with_playwright(
                url,
                timeout=max(timeout, 60),
                headless=playwright_headless,
                wait_seconds=playwright_wait_seconds,
                cdp_url=playwright_cdp_url,
            )
            return result
        raise

    if renderer == 'auto' and _is_challenge_or_block_page(html):
        result = fetch_html_with_playwright(
            url,
            timeout=max(timeout, 60),
            headless=playwright_headless,
            wait_seconds=playwright_wait_seconds,
            cdp_url=playwright_cdp_url,
        )
        return result

    return {'html': html, 'content_type': ctype}


def _save_html_snapshot(url: str, html: str, outdir: Path, page_index: int, timestamp: str) -> str:
    base_name = _build_output_base_name(url, page_index, timestamp)
    html_path = outdir / f"{base_name}.html"

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    return str(html_path)


def _save_page_output(url: str, html: str, outdir: Path, page_index: int, timestamp: str):
    base_name = _build_output_base_name(url, page_index, timestamp)
    html_path = outdir / f"{base_name}.html"
    json_path = outdir / f"{base_name}.json"
    content_blocks = extract_content_blocks(html, url)
    payload = {
        'original_link': url,
        'content_blocks': content_blocks,
        'extra': {},
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    media_count = sum(1 for b in content_blocks if isinstance(b, dict))
    video_count = sum(1 for b in content_blocks if isinstance(b, dict) and b.get('type') == 'video')

    return {
        'html_path': str(html_path),
        'json_path': str(json_path),
        'content_blocks': content_blocks,
        'media_count': media_count,
        'video_count': video_count,
    }


def _failed_dir(outdir: Path) -> Path:
    return outdir / 'failed'


def _manifest_path(start_url: str, outdir: Path) -> Path:
    """最终生成的 manifest JSON 路径（供 analyze_saved_html 消费）。"""
    return outdir / f"{_safe_name_from_url(start_url)}_cache.json"


def _scrape_db_path(start_url: str, outdir: Path) -> Path:
    """合并后的 SQLite 数据库路径（含 pages 和 failed_pages 两张表）。"""
    return outdir / f"{_safe_name_from_url(start_url)}_scrape.db"


def _init_db(db_path: Path) -> sqlite3.Connection:
    """打开（或创建）scrape 数据库，创建 pages 和 failed_pages 两张表，返回连接对象。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pages ("
        "  url TEXT PRIMARY KEY,"
        "  html_path TEXT DEFAULT NULL,"
        "  content_type TEXT NOT NULL DEFAULT '',"
        "  links TEXT DEFAULT NULL,"
        "  video_count INTEGER NOT NULL DEFAULT -1,"
        "  image_count INTEGER NOT NULL DEFAULT -1,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS failed_pages ("
        "  url TEXT PRIMARY KEY,"
        "  reason TEXT NOT NULL,"
        "  html_path TEXT DEFAULT NULL,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"
        ")"
    )
    # 为已有 pages 表补充新增字段
    for col in ('video_count', 'image_count', 'links'):
        try:
            if col == 'links':
                conn.execute(f"ALTER TABLE pages ADD COLUMN {col} TEXT DEFAULT NULL")
            else:
                conn.execute(f"ALTER TABLE pages ADD COLUMN {col} INTEGER NOT NULL DEFAULT -1")
        except Exception:
            pass
    conn.commit()
    return conn


def _load_html_cache_from_db(start_url: str, outdir: Path) -> list:
    """从 SQLite 加载全部页面缓存到内存 list。"""
    db_path = _scrape_db_path(start_url, outdir)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.execute("SELECT url, html_path, content_type FROM pages")
        cache = [{'url': url, 'html_path': html_path, 'content_type': content_type}
                 for url, html_path, content_type in cursor]
        conn.close()
        return cache
    except Exception:
        return []


def _flush_html_batch(conn: sqlite3.Connection, entries: list):
    """批量写（INSERT OR REPLACE）脏页面记录到 SQLite。"""
    if not entries:
        return
    try:
        rows = [(e['url'], e['html_path'], e.get('content_type', '')) for e in entries]
        conn.executemany(
            "INSERT OR REPLACE INTO pages (url, html_path, content_type) VALUES (?, ?, ?)", rows
        )
        conn.commit()
    except Exception:
        pass


def _load_links_cache_from_db(start_url: str, outdir: Path) -> dict:
    """从 SQLite pages 表加载全部链接缓存到内存 dict。"""
    db_path = _scrape_db_path(start_url, outdir)
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.execute("SELECT url, links FROM pages where links IS NOT NULL")
        result = {}
        for url, links_json in cursor:
            try:
                result[url] = json.loads(links_json)
            except Exception:
                result[url] = []
        conn.close()
        return result
    except Exception:
        return {}


def _flush_links_batch(conn: sqlite3.Connection, entries: dict):
    """批量更新 pages 表中的链接列表。"""
    if not entries:
        return
    try:
        rows = [(json.dumps(links, ensure_ascii=False), url) for url, links in entries.items()]
        conn.executemany("UPDATE pages SET links = ? WHERE url = ?", rows)
        conn.commit()
    except Exception:
        pass


def _flush_failed_pages_batch(conn: sqlite3.Connection, entries: list):
    """批量追加写入失败页面记录到 SQLite（INSERT OR REPLACE，使用已有连接）。"""
    if not entries:
        return
    try:
        rows = [
            (p['url'], p['reason'], p.get('html_path', None))
            for p in entries
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO failed_pages (url, reason, html_path) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    except Exception:
        pass


def _load_failed_pages_from_db(start_url: str, outdir: Path) -> list:
    """从 SQLite 加载历史失败页面列表。"""
    db_path = _scrape_db_path(start_url, outdir)
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT url, reason, html_path FROM failed_pages ORDER BY rowid"
        )
        result = [
            {'url': url, 'reason': reason, 'html_path': html_path}
            for url, reason, html_path in cursor
        ]
        conn.close()
        return result
    except Exception:
        return []


def save_site_html(
    url: str,
    outdir: Path,
    max_depth: int = 0,
    max_pages: int = 20,
    max_concurrency: int = 1,
    renderer: str = 'auto',
    playwright_headless: bool = True,
    playwright_wait_seconds: float = 5.0,
    playwright_cdp_url: str = '',
    phase_callback=None,
):
    start_url = _normalize_url(url)
    if not start_url:
        raise ValueError('Only http/https URLs are supported.')

    outdir.mkdir(parents=True, exist_ok=True)
    _failed_dir(outdir).mkdir(parents=True, exist_ok=True)
    set_log_file(outdir / 'scrape.log')
    root_host = urlparse(start_url).netloc
    visited = set()
    queue = deque()
    failed_pages: list = _load_failed_pages_from_db(start_url, outdir)
    failed_urls: set = {p['url'] for p in failed_pages}
    _log(f'Loaded {len(failed_urls)} failed pages from SQLite.')
    html_cache = _load_html_cache_from_db(start_url, outdir)
    _log(f'Loaded {len(html_cache)} cached pages from SQLite.')
    links_cache = _load_links_cache_from_db(start_url, outdir)
    _log(f'Loaded {len(links_cache)} cached links from SQLite.')
    conn = _init_db(_scrape_db_path(start_url, outdir))  # 单个持久连接，含 pages + failed_pages 两张表
    _log(f'Initialized SQLite database at {_scrape_db_path(start_url, outdir)}.')
    dirty_html: list = []  # 自上次 flush 后新增的页面记录
    dirty_links: dict = {}  # 记录自上次 flush 后变更的 url -> links
    dirty_failed: list = []  # 自上次 flush 后新增的失败页面记录
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')

    # 从本地缓存恢复：优先使用 links_cache 避免重新解析 HTML
    for cached in html_cache:
        cached_url = cached['url']
        # 只读取 content_type 为 html 的文件来提取链接
        ctype = cached.get('content_type', '')
        if not HTML_CONTENT_TYPE_RE.search(ctype):
            visited.add(cached_url)
            _log(f'跳过非 HTML 缓存 URL: {cached_url} (content_type={ctype})')
            continue
        # 优先从 links_cache 读取，否则回退到解析 HTML
        links = links_cache.get(cached_url, None)
        if cached['html_path'] is None:
            #_log(f'缓存记录 html_path 为空: {cached_url}')
            continue
        cached_html_path = Path(cached['html_path'])
        if cached_html_path.exists():
            visited.add(cached_url)
        else:
            #_log(f'缓存 HTML 文件不存在: {cached_html_path} (URL: {cached_url})')
            continue
        if links is None:
            try:
                _log(f'Processing cached URL: {cached_url}')
                cached_html = cached_html_path.read_text(encoding='utf-8')
                # 遇到links!=[]的并且是block/challenge页面的情况，并不会执行到这里，需要单独写个程序洗出 block/challenge页面的缓存
                if _is_challenge_or_block_page(cached_html):
                    _log(f'Cached HTML is a challenge/block page: {cached_url}')
                    cached_html_path.unlink(missing_ok=True)
                    continue
                links = _extract_links(cached_html, cached_url, root_host)
                dirty_links[cached_url] = links
            except Exception:
                links = []

        for link in links:
            if link not in visited and link not in failed_urls:
                # 从 URL 路径解析深度（path 分段数)
                cached_path = urlparse(link).path.strip('/')
                cached_depth = len(cached_path.split('/')) if cached_path else 0
                queue.append((link, cached_depth))

    # 如果 start_url 不在缓存中，加入队列
    if start_url not in visited and start_url not in failed_urls:
        queue.appendleft((start_url, 0))
    unlimited_depth = max_depth < 0
    unlimited_pages = max_pages <= 0
    max_concurrency = max(1, int(max_concurrency))

    def _append_failed(page_url: str, reason: str, html: str = ''):
        failed_html_path = ''
        if html:
            try:
                failed_html_path = _save_html_snapshot(
                    page_url,
                    html,
                    _failed_dir(outdir),
                    page_index=len(failed_urls) + 1,
                    timestamp=timestamp,
                )
            except Exception:
                failed_html_path = ''
        entry = {
            'url': page_url,
            'reason': reason,
            'html_path': failed_html_path,
        }
        dirty_failed.append(entry)
        failed_urls.add(page_url)

    def _fetch_one(item):
        page_url, _depth = item
        try:
            fetch_result = fetch_html(
                page_url,
                renderer=renderer,
                playwright_headless=playwright_headless,
                playwright_wait_seconds=playwright_wait_seconds,
                playwright_cdp_url=playwright_cdp_url,
            )
            html_text = fetch_result['html']
            content_type = fetch_result.get('content_type', '')
            # 在线程内提取链接，避免主线程串行解析
            links = None
            if HTML_CONTENT_TYPE_RE.search(content_type):
                try:
                    links = _extract_links(html_text, page_url, root_host)
                except Exception:
                    links = None
            return {
                'url': page_url,
                'html': html_text,
                'html_path': None,
                'error': '',
                'content_type': content_type,
                'links': links,
            }
        except Exception as exc:
            return {
                'url': page_url,
                'html': '',
                'html_path': None,
                'error': str(exc),
                'content_type': '',
                'links': None,
            }

    if callable(phase_callback):
        phase_callback('saving_html')

    pending = {}  # {Future: (url, depth)}

    def _try_submit():
        """从队列取一个 URL 提交到线程池，返回是否成功提交。"""
        while queue:
            if not unlimited_pages and len(html_cache) >= max_pages:
                return False
            current_url, depth = queue.popleft()
            if not unlimited_depth and depth > max_depth:
                continue
            if current_url in visited:
                continue
            if not re.search(r'\.html?$', urlparse(current_url).path, re.IGNORECASE) and '.' in Path(urlparse(current_url).path).suffix:
                continue
            visited.add(current_url)
            future = executor.submit(_fetch_one, (current_url, depth))
            pending[future] = (current_url, depth)
            _log(f'提交线程: {current_url} (深度 {depth}), 待处理: {len(pending)}')
            return True
        return False

    def _flush_dirty():
        if dirty_html or dirty_links or dirty_failed:
            _log(f'批量保存到 SQLite: {len(dirty_html)} HTML, {len(dirty_links)} links, {len(dirty_failed)} failed')
            _flush_html_batch(conn, dirty_html)
            dirty_html.clear()
            _flush_links_batch(conn, dirty_links)
            dirty_links.clear()
            _flush_failed_pages_batch(conn, dirty_failed)
            dirty_failed.clear()

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        # 初始填满线程池
        while len(pending) < max_concurrency:
            if not _try_submit():
                break

        while pending:
            # 等待至少一个线程完成
            done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)

            for future in done:
                current_url, depth = pending.pop(future)
                item = future.result()

                if not item:
                    _log(f'未获取到页面: {current_url}')
                    continue

                html = item.get('html', '')
                fetch_error = item.get('error', '')
                content_type = item.get('content_type', '')

                if fetch_error or not content_type:
                    _log(f'抓取页面失败: {current_url}, 错误: {fetch_error}')
                    _append_failed(current_url, fetch_error, html)
                    continue

                if HTML_CONTENT_TYPE_RE.search(content_type) and _is_challenge_or_block_page(html):
                    _log(f'挑战或封锁页面: {current_url}')
                    _append_failed(current_url, 'challenge_or_block', html)
                    continue

                if HTML_CONTENT_TYPE_RE.search(content_type) and not _is_html_document(html):
                    _log(f'非 HTML 文档: {current_url}')
                    _append_failed(current_url, 'not_html_document', html)
                    continue

                if HTML_CONTENT_TYPE_RE.search(content_type):
                    page_index = len(html_cache) + 1
                    _log(f"正在保存 HTML: {current_url} (深度 {depth}, 页面索引 {page_index})")
                    try:
                        html_path = _save_html_snapshot(
                            current_url,
                            html,
                            outdir,
                            page_index=page_index,
                            timestamp=timestamp,
                        )
                    except Exception as e:
                        _log(f'保存 HTML 失败: {current_url}, 错误: {e}')
                        continue
                else:
                    _log(f'非 HTML 内容类型: {current_url}, content_type={content_type}')
                    html_path = None

                html_cache.append({'url': current_url, 'html_path': html_path, 'content_type': content_type})
                dirty_html.append({'url': current_url, 'html_path': html_path, 'content_type': content_type})

                links = item.get('links', None)
                if HTML_CONTENT_TYPE_RE.search(content_type) and links is not None:
                    dirty_links[current_url] = links

                if links:
                    for link in links:
                        if link not in visited and link not in failed_urls:
                            cached_path = urlparse(link).path.strip('/')
                            cached_depth = len(cached_path.split('/')) if cached_path else 0
                            queue.append((link, cached_depth))

            _flush_dirty()
            # 此线程完成，有空闲槽位，立即提交新任务
            while len(pending) < max_concurrency:
                if not _try_submit():
                    break

            

    # 最终刷写
    _flush_html_batch(conn, dirty_html)
    dirty_html.clear()
    _flush_links_batch(conn, dirty_links)
    dirty_links.clear()
    _flush_failed_pages_batch(conn, dirty_failed)
    dirty_failed.clear()
    conn.close()
    # 写入最终 manifest JSON 供 analyze_saved_html 消费
    manifest_path = _manifest_path(start_url, outdir)
    manifest_payload = {
        'start_url': start_url,
        'saved_count': len(html_cache),
        'pages': [{'url': p['url'], 'html_path': p['html_path'], 'content_type': p.get('content_type', '')}
                  for p in html_cache if p['html_path'] is not None],
    }
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest_payload, f, ensure_ascii=False, indent=2)

    result = {
        'start_url': start_url,
        'saved_count': len(html_cache),
        'pages': html_cache,
    }
    result['failed_count'] = len(failed_urls)
    result['summary_path'] = str(manifest_path)
    return result


def analyze_saved_html(manifest_path: Path, progress_callback=None, phase_callback=None):
    manifest_path = Path(manifest_path)
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    pages = []
    analysis_failed = []
    analysis_failed_reasons = {}
    total_videos = 0
    total_media = 0
    manifest_pages = manifest.get('pages', [])
    total_known_html = len(manifest_pages)

    if callable(phase_callback):
        phase_callback('analyzing_html')

    for idx, page in enumerate(manifest_pages, start=1):
        current_url = page['url']
        if callable(progress_callback):
            progress_callback(idx, total_known_html, current_url)

        if page.get('html_path') is None:
            if current_url not in analysis_failed_reasons:
                analysis_failed.append(current_url)
            analysis_failed_reasons[current_url] = 'html_path_is_null'
            continue

        html_path = Path(page['html_path'])
        try:
            html = html_path.read_text(encoding='utf-8')
        except Exception:
            if current_url not in analysis_failed_reasons:
                analysis_failed.append(current_url)
            analysis_failed_reasons[current_url] = 'html_read_error'
            continue

        try:
            page_data = _save_page_output(
                current_url,
                html,
                html_path.parent,
                idx,
                html_path.stem.rsplit('_html_', 1)[-1],
            )
        except Exception:
            if current_url not in analysis_failed_reasons:
                analysis_failed.append(current_url)
            analysis_failed_reasons[current_url] = 'parse_or_save_error'
            continue

        total_videos += page_data['video_count']
        total_media += page_data['media_count']
        pages.append(
            {
                'url': current_url,
                'html_path': page_data['html_path'],
                'json_path': page_data['json_path'],
                'video_count': page_data['video_count'],
                'media_count': page_data['media_count'],
            }
        )

    summary = {
        'start_url': manifest['start_url'],
        'manifest_path': str(manifest_path),
        'page_count': len(pages),
        'video_count': total_videos,
        'media_count': total_media,
        'failed_count': len(analysis_failed),
        'failed': analysis_failed,
        'failed_reasons': analysis_failed_reasons,
        'pages': pages,
    }
    summary_path = manifest_path.parent / f"{_safe_name_from_url(manifest['start_url'])}_summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    summary['summary_path'] = str(summary_path)
    return summary


def scrape_site(
    url: str,
    outdir: Path,
    max_depth: int = 0,
    max_pages: int = 20,
    max_concurrency: int = 1,
    renderer: str = 'auto',
    playwright_headless: bool = True,
    playwright_wait_seconds: float = 5.0,
    playwright_cdp_url: str = '',
    progress_callback=None,
    phase_callback=None,
):
    save_result = save_site_html(
        url,
        outdir,
        max_depth=max_depth,
        max_pages=max_pages,
        max_concurrency=max_concurrency,
        renderer=renderer,
        playwright_headless=playwright_headless,
        playwright_wait_seconds=playwright_wait_seconds,
        playwright_cdp_url=playwright_cdp_url,
        phase_callback=phase_callback,
    )
    summary = analyze_saved_html(
        save_result['summary_path'],
        progress_callback=progress_callback,
        phase_callback=phase_callback,
    )
    return summary


def scrape_url(url: str, outdir: Path):
    result = scrape_site(url, outdir, max_depth=0, max_pages=1)
    first_page = result['pages'][0] if result['pages'] else {}
    return {
        'html_path': first_page.get('html_path', ''),
        'json_path': first_page.get('json_path', ''),
        'page_count': result['page_count'],
        'video_count': result['video_count'],
        'media_count': result.get('media_count', 0),
        'summary_path': result.get('summary_path', ''),
    }
