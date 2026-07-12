import json
import re
from datetime import datetime
import hashlib
from collections import deque
from pathlib import Path
import time
from urllib.parse import urldefrag, urljoin, urlparse

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
    'form',
    'noscript',
}

NOISE_KEYWORDS = ('nav', 'footer', 'cookie', 'consent', 'breadcrumb', 'share', 'related')

VIDEO_FILE_RE = re.compile(r'\.(mp4|m3u8|webm|ogg)(\?|$)', re.I)
EMBED_RE = re.compile(r'youtube|youtu\.be|vimeo|player|wistia', re.I)
IMAGE_FILE_RE = re.compile(r'\.(png|jpe?g|webp|gif|bmp|svg)(\?|$)', re.I)
PDF_FILE_RE = re.compile(r'\.pdf(\?|$)', re.I)
NON_HTML_FILE_RE = re.compile(
    r'\.(pdf|zip|rar|7z|tar|gz|docx?|xlsx?|pptx?|csv|xml|json|txt|css|js|map|ico|woff2?|ttf|eot|otf|mp3|wav|ogg|mp4|m3u8|webm|avi|mov)(\?|$)',
    re.I,
)
HTML_CONTENT_TYPE_RE = re.compile(r'text/html|application/xhtml\+xml', re.I)


def _safe_name_from_url(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.replace(':', '_')
    path = (p.path.strip('/').replace('/', '_') or 'index')
    query = re.sub(r'[^a-zA-Z0-9]+', '_', p.query).strip('_')
    if query:
        return f"{host}_{path}_{query}"
    return f"{host}_{path}"


def _path_name_from_url(url: str) -> str:
    p = urlparse(url)
    path = (p.path.strip('/').replace('/', '_') or 'index')
    query = re.sub(r'[^a-zA-Z0-9]+', '_', p.query).strip('_')
    if query:
        return f"{path}_{query}"
    return path


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
        if _is_in_noise_area(a):
            continue
        href = a.get('href', '').strip()
        if not href or href.startswith('#'):
            continue
        if href.startswith(('mailto:', 'tel:', 'javascript:')):
            continue
        resolved = _normalize_url(_resolve_url(base_url, href))
        if not resolved:
            continue
        if PDF_FILE_RE.search(resolved):
            continue
        # Keep likely HTML pages: .html/.htm, trailing slash, or extensionless paths.
        # Skip obvious non-HTML file resources.
        if NON_HTML_FILE_RE.search(resolved):
            continue
        if not _is_same_domain(resolved, root_host):
            continue
        links.append(resolved)
    return links


def _mark_failed(url: str, reason: str, failed: list, failed_reasons: dict):
    if url not in failed_reasons:
        failed.append(url)
    failed_reasons[url] = reason


def extract_videos(html: str, base_url: str):
    soup = BeautifulSoup(html, 'html.parser')
    return _extract_video_items(soup, base_url)


def _is_html_document(html: str) -> bool:
    sample = (html or '')[:4000].lower()
    return ('<html' in sample) or ('<!doctype html' in sample)


def _is_challenge_or_block_page(html: str) -> bool:
    sample = (html or '')[:6000].lower()
    markers = (
        'just a moment',
        'cf-browser-verification',
        'attention required',
        'checking your browser',
        '/cdn-cgi/challenge-platform',
        'enable javascript and cookies',
    )
    return any(m in sample for m in markers)


def fetch_html_with_playwright(url: str, timeout=30, wait_seconds: float = 5.0, headless: bool = True) -> str:
    if sync_playwright is None:
        raise RuntimeError('Playwright is not installed. Run: pip install playwright and playwright install chromium')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        )
        page = context.new_page()
        response = page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        if response is not None:
            ctype = response.headers.get('content-type', '')
            if ctype and not HTML_CONTENT_TYPE_RE.search(ctype):
                raise RuntimeError(f'Non-HTML content type: {ctype}')
        # Allow challenge scripts and async rendering to complete.
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        html = page.content()
        context.close()
        browser.close()
    return html


def fetch_html(
    url: str,
    timeout=15,
    renderer: str = 'auto',
    playwright_headless: bool = True,
    playwright_wait_seconds: float = 5.0,
) -> str:
    headers = {
        'User-Agent': 'python-httrack/1.0 (+https://example.com)'
    }
    if renderer == 'playwright':
        html = fetch_html_with_playwright(
            url,
            timeout=max(timeout, 20),
            headless=playwright_headless,
            wait_seconds=playwright_wait_seconds,
        )
        if _is_challenge_or_block_page(html):
            raise RuntimeError(f'Blocked by target site after Playwright rendering: {url}')
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
                timeout=max(timeout, 20),
                headless=playwright_headless,
                wait_seconds=playwright_wait_seconds,
            )
            if _is_challenge_or_block_page(html):
                raise RuntimeError(f'Blocked by target site after Playwright rendering: {url}')
            return html
        raise

    if renderer == 'auto' and _is_challenge_or_block_page(html):
        html = fetch_html_with_playwright(
            url,
            timeout=max(timeout, 20),
            headless=playwright_headless,
            wait_seconds=playwright_wait_seconds,
        )
        if _is_challenge_or_block_page(html):
            raise RuntimeError(f'Blocked by target site after Playwright rendering: {url}')

    return html


def _save_page_output(url: str, html: str, outdir: Path, page_index: int, timestamp: str):
    page_name = _path_name_from_url(url)
    base_name = f"{page_index:04d}_{page_name}_html_{timestamp}"
    html_path = outdir / f"{base_name}.html"
    json_path = outdir / f"{base_name}.json"

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

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


def scrape_site(
    url: str,
    outdir: Path,
    max_depth: int = 0,
    max_pages: int = 20,
    renderer: str = 'auto',
    playwright_headless: bool = True,
    playwright_wait_seconds: float = 5.0,
    progress_callback=None,
):
    start_url = _normalize_url(url)
    if not start_url:
        raise ValueError('Only http/https URLs are supported.')

    outdir.mkdir(parents=True, exist_ok=True)
    root_host = urlparse(start_url).netloc

    visited = set()
    queue = deque([(start_url, 0)])
    pages = []
    failed = []
    failed_reasons = {}
    total_videos = 0
    total_media = 0
    discovered_urls = []
    html_cache = {}
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    unlimited_depth = max_depth < 0
    unlimited_pages = max_pages <= 0

    # Phase 1: recursively discover and fetch all HTML pages to establish a stable total.
    while queue and (unlimited_pages or len(discovered_urls) < max_pages):
        current_url, depth = queue.popleft()
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            html = fetch_html(
                current_url,
                renderer=renderer,
                playwright_headless=playwright_headless,
                playwright_wait_seconds=playwright_wait_seconds,
            )
        except Exception as exc:
            _mark_failed(current_url, str(exc), failed, failed_reasons)
            continue

        if not _is_html_document(html):
            _mark_failed(current_url, 'non_html_document', failed, failed_reasons)
            continue

        discovered_urls.append(current_url)
        html_cache[current_url] = html

        if not unlimited_depth and depth >= max_depth:
            continue

        try:
            links = _extract_links(html, current_url, root_host)
        except Exception:
            _mark_failed(current_url, 'link_extract_error', failed, failed_reasons)
            links = []

        for link in links:
            if link not in visited:
                queue.append((link, depth + 1))

    # Phase 2: parse and save with fixed total HTML count.
    total_known_html = len(discovered_urls)
    for idx, current_url in enumerate(discovered_urls, start=1):
        if callable(progress_callback):
            progress_callback(idx, total_known_html, current_url)

        html = html_cache[current_url]
        try:
            page_data = _save_page_output(
                current_url,
                html,
                outdir,
                page_index=len(pages) + 1,
                timestamp=timestamp,
            )
        except Exception:
            _mark_failed(current_url, 'parse_or_save_error', failed, failed_reasons)
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
        'start_url': start_url,
        'page_count': len(pages),
        'video_count': total_videos,
        'media_count': total_media,
        'failed_count': len(failed),
        'failed': failed,
        'failed_reasons': failed_reasons,
        'pages': pages,
    }
    summary_path = outdir / f"{_safe_name_from_url(start_url)}_summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    summary['summary_path'] = str(summary_path)
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
