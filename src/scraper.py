import json
import re
from datetime import datetime
import hashlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
from urllib.parse import urldefrag, urljoin, urlparse, unquote

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
HTML_CONTENT_TYPE_RE = re.compile(r'text/html|application/xhtml\+xml', re.I)
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/138.0.0.0 Safari/537.36'
)


def _log(message: str):
    now = datetime.now().strftime('%H:%M:%S')
    print(f'[{now}] {message}')


def _safe_name_from_url(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.replace(':', '_')
    path = _sanitize_filename_component(unquote(p.path.strip('/').replace('/', '_')) or '')
    raw_query = unquote(re.sub(r'[^a-zA-Z0-9]+', '_', p.query).strip('_'))
    query = _sanitize_filename_component(raw_query) if raw_query else ''
    if path and query:
        return f"{host}_{path}_{query}"
    if path:
        return f"{host}_{path}"
    if query:
        return f"{host}_{query}"
    return host


def _path_name_from_url(url: str) -> str:
    p = urlparse(url)
    path = _sanitize_filename_component(unquote(p.path.strip('/').replace('/', '_')) or '')
    raw_query = unquote(re.sub(r'[^a-zA-Z0-9]+', '_', p.query).strip('_'))
    query = _sanitize_filename_component(raw_query) if raw_query else ''
    if path and query:
        return f"{path}_{query}"
    if path:
        return path
    if query:
        return query
    return 'index'


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
    sample = (html or '')[:6000].lower()
    strong_markers = (
        'just a moment',
        'cf-browser-verification',
        'attention required',
        'checking your browser',
        '/cdn-cgi/challenge-platform',
        'challenges.cloudflare.com',
        '__cf_chl_',
        'cf-chl-',
        'turnstile',
        'enable javascript and cookies',
        '正在进行安全验证',
        '请验证您是真人',
        '安全服务防护恶意自动程序',
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
) -> str:
    if sync_playwright is None:
        raise RuntimeError('Playwright is not installed. Run: pip install playwright and playwright install chromium')

    with sync_playwright() as p:
        _log(f'Playwright启动: {url}')
        use_cdp = bool(cdp_url)
        if use_cdp:
            _log(f'通过CDP连接Chrome: {cdp_url}')
            browser = p.chromium.connect_over_cdp(cdp_url)
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = browser.new_context(ignore_https_errors=True, user_agent=DEFAULT_USER_AGENT)
        else:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                ignore_https_errors=True,
                user_agent=DEFAULT_USER_AGENT,
            )
        page = context.new_page()
        _log(f'开始打开页面: {url}')
        response = page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        if response is not None:
            ctype = response.headers.get('content-type', '')
            if ctype and not HTML_CONTENT_TYPE_RE.search(ctype):
                raise RuntimeError(f'Non-HTML content type: {ctype}')
        try:
            page.wait_for_load_state('load', timeout=timeout * 1000)
        except Exception:
            # Some sites keep loading long-poll resources; proceed with timed readiness checks.
            pass

        body_deadline = time.time() + max(float(timeout), 10.0)

        def _capture_current_page_html() -> str:
            current_html = page.content()

            # Wait until document body becomes readable.
            while time.time() < body_deadline:
                current_html = page.content()
                lowered = current_html.lower()
                has_body = '<body' in lowered
                if not has_body:
                    page.wait_for_timeout(1000)
                    continue
                break

            current_html = page.content()

            # 用户设置的额外等待应精确生效，不再隐式叠加额外秒数。
            settle_wait_seconds = max(float(wait_seconds), 0.0)

            # Only when a challenge page is detected do we wait longer for verification.
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
                _log(f'未检测到挑战页，额外等待{settle_wait_seconds:.1f}秒: {url}')
                if settle_wait_seconds > 0:
                    time.sleep(settle_wait_seconds)

            return page.content()

        html = _capture_current_page_html()
        html_is_document = _is_html_document(html)
        html_is_challenge = _is_challenge_or_block_page(html)

        # Avoid forced reload when challenge is still on screen. Reloading often
        # resets a manually completed challenge and triggers a new one.
        if not html_is_document:
            if time.time() < body_deadline:
                _log(f'读取HTML未成功，刷新后重试一次: {url}')
                try:
                    response = page.reload(wait_until='domcontentloaded', timeout=timeout * 1000)
                    if response is not None:
                        ctype = response.headers.get('content-type', '')
                        if ctype and not HTML_CONTENT_TYPE_RE.search(ctype):
                            raise RuntimeError(f'Non-HTML content type: {ctype}')
                    try:
                        page.wait_for_load_state('load', timeout=timeout * 1000)
                    except Exception:
                        pass
                    html = _capture_current_page_html()
                except Exception as exc:
                    _log(f'刷新重试失败，继续使用首次读取结果: {url} -> {exc}')
            else:
                _log(f'读取HTML超时，继续使用首次读取结果: {url}')
        elif html_is_challenge:
            _log(f'挑战页仍未通过，不执行自动刷新以避免触发新一轮验证: {url}')
        _log(f'HTML已抓取，准备关闭page: {url}, 字节数: {len(html)}')
        page.close()
        if use_cdp:
            browser.close()
            _log(f'CDP连接已断开: {url}')
        else:
            context.close()
            browser.close()
            _log(f'浏览器已关闭: {url}')
    return html


def fetch_html(
    url: str,
    timeout=60,
    renderer: str = 'auto',
    playwright_headless: bool = True,
    playwright_wait_seconds: float = 5.0,
    playwright_cdp_url: str = '',
) -> str:
    headers = {
        'User-Agent': DEFAULT_USER_AGENT
    }
    if renderer == 'playwright':
        html = fetch_html_with_playwright(
            url,
            timeout=max(timeout, 60),
            headless=playwright_headless,
            wait_seconds=playwright_wait_seconds,
            cdp_url=playwright_cdp_url,
        )
        return html

    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        ctype = r.headers.get('content-type', '')
        if ctype and not HTML_CONTENT_TYPE_RE.search(ctype):
            raise RuntimeError(f'Non-HTML content type: {ctype}')
        # Many sites return missing/incorrect charset headers. Prefer apparent encoding
        # so Unicode punctuation like em dash is preserved in saved JSON.
        if not r.encoding or r.encoding.lower() in ('iso-8859-1', 'latin1'):
            r.encoding = r.apparent_encoding or 'utf-8'
        html = r.text
    except requests.RequestException:
        if renderer == 'auto':
            html = fetch_html_with_playwright(
                url,
                timeout=max(timeout, 60),
                headless=playwright_headless,
                wait_seconds=playwright_wait_seconds,
                cdp_url=playwright_cdp_url,
            )
            return html
        raise

    if renderer == 'auto' and _is_challenge_or_block_page(html):
        html = fetch_html_with_playwright(
            url,
            timeout=max(timeout, 60),
            headless=playwright_headless,
            wait_seconds=playwright_wait_seconds,
            cdp_url=playwright_cdp_url,
        )

    return html


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


def _failed_pages_path(start_url: str, outdir: Path) -> Path:
    return _failed_dir(outdir) / f"{_safe_name_from_url(start_url)}_failed_pages.json"


def _load_failed_pages(start_url: str, outdir: Path):
    failed_path = _failed_pages_path(start_url, outdir)
    if not failed_path.exists():
        legacy_path = outdir / f"{_safe_name_from_url(start_url)}_failed_pages.json"
        failed_path = legacy_path if legacy_path.exists() else failed_path
    if not failed_path.exists():
        return []

    try:
        with open(failed_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception:
        return []

    items = payload.get('failed_pages', []) if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def _write_failed_pages_manifest(start_url: str, outdir: Path, failed_pages: list):
    failed_path = _failed_pages_path(start_url, outdir)
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'start_url': start_url,
        'failed_count': len(failed_pages),
        'failed_pages': failed_pages,
    }
    with open(failed_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(failed_path)


def _html_cache_path(start_url: str, outdir: Path) -> Path:
    return outdir / f"{_safe_name_from_url(start_url)}_cache.json"


def _load_html_cache(start_url: str, outdir: Path):
    cache_path = _html_cache_path(start_url, outdir)
    if not cache_path.exists():
        return []

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception:
        return []

    entries = payload.get('pages', []) if isinstance(payload, dict) else []
    cache = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        page_url = item.get('url', '')
        html_path = item.get('html_path', '')
        if not page_url or not html_path:
            continue
        if Path(html_path).exists():
            cache.append({'url': page_url, 'html_path': html_path})
    return cache


def _write_html_cache(start_url: str, outdir: Path, cache: list):
    cache_path = _html_cache_path(start_url, outdir)
    pages = [item for item in cache if isinstance(item, dict) and item.get('url') and item.get('html_path')]
    payload = {
        'start_url': start_url,
        'saved_count': len(pages),
        'pages': pages,
    }
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


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
    root_host = urlparse(start_url).netloc
    visited = set()
    queue = deque([(start_url, 0)])
    queued = {start_url}
    failed_pages = _load_failed_pages(start_url, outdir)
    html_cache = _load_html_cache(start_url, outdir)
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    unlimited_depth = max_depth < 0
    unlimited_pages = max_pages <= 0
    max_concurrency = max(1, int(max_concurrency))

    def _cached_entry_for(page_url: str):
        for item in html_cache:
            if item.get('url') == page_url:
                return item
        return None

    def _append_failed(page_url: str, reason: str, html: str = ''):
        failed_html_path = ''
        if html:
            try:
                failed_html_path = _save_html_snapshot(
                    page_url,
                    html,
                    _failed_dir(outdir),
                    page_index=len(failed_pages) + 1,
                    timestamp=timestamp,
                )
            except Exception:
                failed_html_path = ''
        failed_pages.append(
            {
                'index': len(failed_pages) + 1,
                'url': page_url,
                'reason': reason,
                'html_path': failed_html_path,
            }
        )
        _write_failed_pages_manifest(start_url, outdir, failed_pages)

    def _fetch_one(item):
        page_url, _depth = item
        try:
            html_text = fetch_html(
                page_url,
                renderer=renderer,
                playwright_headless=playwright_headless,
                playwright_wait_seconds=playwright_wait_seconds,
                playwright_cdp_url=playwright_cdp_url,
            )
            return {
                'url': page_url,
                'html': html_text,
                'html_path': '',
                'error': '',
            }
        except Exception as exc:
            return {
                'url': page_url,
                'html': '',
                'html_path': '',
                'error': str(exc),
            }

    if callable(phase_callback):
        phase_callback('saving_html')

    while queue and (unlimited_pages or len(html_cache) < max_pages):
        if unlimited_pages:
            per_round = min(max_concurrency, len(queue))
        else:
            remaining = max_pages - len(html_cache)
            if remaining <= 0:
                break
            per_round = min(max_concurrency, len(queue), remaining)

        batch = []
        while queue and len(batch) < per_round:
            current_url, depth = queue.popleft()
            queued.discard(current_url)
            if current_url in visited:
                continue
            if not re.search(r'\.html?$', urlparse(current_url).path, re.IGNORECASE) and '.' in Path(urlparse(current_url).path).suffix:
                continue
            visited.add(current_url)
            batch.append((current_url, depth))

        if not batch:
            continue

        result_by_url = {}
        to_fetch = []
        for current_url, _depth in batch:
            html = ''
            html_path = ''
            cached = _cached_entry_for(current_url)
            cached_path = cached.get('html_path', '') if cached else ''
            if cached_path:
                try:
                    html = Path(cached_path).read_text(encoding='utf-8')
                    html_path = cached_path
                except Exception:
                    html = ''
                    html_path = ''

            if html:
                result_by_url[current_url] = {
                    'url': current_url,
                    'html': html,
                    'html_path': html_path,
                    'error': '',
                }
            else:
                to_fetch.append((current_url, _depth))

        if to_fetch:
            worker_count = min(max_concurrency, len(to_fetch))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                fetched_results = list(executor.map(_fetch_one, to_fetch))
            for item in fetched_results:
                result_by_url[item['url']] = item

        for current_url, depth in batch:
            item = result_by_url.get(current_url)
            if not item:
                continue

            html = item.get('html', '')
            html_path = item.get('html_path', '')
            fetch_error = item.get('error', '')

            if fetch_error:
                failed_html = (
                    '<!doctype html><html><head><meta charset="utf-8"></head><body>'
                    f'<h1>fetch_failed</h1><p>{fetch_error}</p>'
                    '</body></html>'
                )
                _append_failed(current_url, fetch_error, failed_html)
                continue

            if not _is_html_document(html) or _is_challenge_or_block_page(html):
                reason = 'non_html_document' if not _is_html_document(html) else 'challenge_or_block'
                _append_failed(current_url, reason, html)
                continue

            if not html_path:
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
                except Exception:
                    continue

                html_cache.append({'url': current_url, 'html_path': html_path})
                _write_html_cache(start_url, outdir, html_cache)

            if not unlimited_depth and depth >= max_depth:
                continue

            try:
                links = _extract_links(html, current_url, root_host)
            except Exception:
                links = []

            for link in links:
                if link not in visited and link not in queued:
                    queue.append((link, depth + 1))
                    queued.add(link)

    _write_html_cache(start_url, outdir, html_cache)
    cache_path = _html_cache_path(start_url, outdir)
    result = {
        'start_url': start_url,
        'saved_count': len(html_cache),
        'pages': html_cache,
    }
    result['failed_count'] = len(failed_pages)
    result['summary_path'] = str(cache_path)
    result['failed_pages_path'] = str(_failed_pages_path(start_url, outdir))
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
