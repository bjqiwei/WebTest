import json
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


def _safe_name_from_url(url: str) -> str:
    p = urlparse(url)
    host = p.netloc.replace(':', '_')
    path = (p.path.strip('/').replace('/', '_') or 'index')
    return f"{host}_{path}"


def _resolve_url(base: str, link: str) -> str:
    if not link:
        return ''
    return urljoin(base, link)


def _nearby_description(tag):
    # prefer figcaption
    fig = tag.find_parent(['figure'])
    if fig:
        cap = fig.find('figcaption')
        if cap and cap.get_text(strip=True):
            return cap.get_text(strip=True)

    # check alt/title
    if tag.has_attr('alt') and tag['alt'].strip():
        return tag['alt'].strip()
    if tag.has_attr('title') and tag['title'].strip():
        return tag['title'].strip()

    # previous sibling text
    prev = tag.find_previous(string=True)
    if prev and prev.strip():
        return prev.strip()

    # fallback: parent text
    parent_text = tag.parent.get_text(strip=True) if tag.parent else ''
    return parent_text[:200]


def extract_videos(html: str, base_url: str):
    soup = BeautifulSoup(html, 'html.parser')
    videos = []

    # <video> tags
    for vid in soup.find_all('video'):
        src = ''
        if vid.has_attr('src'):
            src = vid['src']
        else:
            source = vid.find('source')
            if source and source.has_attr('src'):
                src = source['src']

        src = _resolve_url(base_url, src)
        desc = _nearby_description(vid)
        if src:
            videos.append({'url': src, 'description': desc})

    # <iframe> embeds (YouTube, Vimeo, etc.)
    for iframe in soup.find_all('iframe'):
        src = iframe.get('src')
        if not src:
            continue
        src = _resolve_url(base_url, src)
        if re.search(r'youtube|vimeo|player', src, re.I) or src.endswith('.mp4'):
            desc = _nearby_description(iframe)
            videos.append({'url': src, 'description': desc})

    # direct links to video files
    for a in soup.find_all('a', href=True):
        href = a['href']
        if re.search(r'\.(mp4|m3u8|webm|ogg)(\?|$)', href, re.I):
            src = _resolve_url(base_url, href)
            desc = _nearby_description(a)
            videos.append({'url': src, 'description': desc})

    # remove duplicates while preserving order
    seen = set()
    filtered = []
    for v in videos:
        if v['url'] and v['url'] not in seen:
            seen.add(v['url'])
            filtered.append(v)

    return filtered


def fetch_html(url: str, timeout=15) -> str:
    headers = {
        'User-Agent': 'python-httrack/1.0 (+https://example.com)'
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def scrape_url(url: str, outdir: Path):
    html = fetch_html(url)
    name = _safe_name_from_url(url)
    html_path = outdir / f"{name}.html"
    json_path = outdir / f"{name}.json"

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    videos = extract_videos(html, url)

    out = {'source': url, 'videos': videos}
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    return {'html_path': str(html_path), 'json_path': str(json_path), 'videos': videos}
