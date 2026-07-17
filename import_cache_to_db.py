"""
Demo 脚本：将已有的 _cache.json manifest 导入 SQLite _scrape.db

用法:
    python import_cache_to_db.py d:/unicef

流程:
    1. 读取 www.unicef.org_cache.json → pages (url, html_path, content_type)
    2. 写入 pages 表
    3. 读取每个 html 文件提取链接 → 写入 links 表

结果：生成 www.unicef.org_scrape.db，后续可直接被 save_site_html 重用。
"""

import json
import sqlite3
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup


# ---------- 从 scraper.py 复用的辅助函数 ----------

HTML_CONTENT_TYPE_RE = re.compile(
    r'^\s*(?:text/html|application/xhtml\+xml)\s*(?:;|$)', re.I
)


def _normalize_url(url: str) -> str:
    from urllib.parse import urldefrag

    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return ''
    path = parsed.path or '/'
    return parsed._replace(path=path, fragment='').geturl()


def _resolve_url(base: str, link: str) -> str:
    if not link:
        return ''
    return urljoin(base, link)


def _is_same_domain(url: str, root_host: str) -> bool:
    host = urlparse(url).netloc.lower()
    root = root_host.lower()
    return host == root or host.endswith(f'.{root}')


def _extract_links(html: str, base_url: str, root_host: str):
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        if not href or href.startswith('#'):
            continue
        if href.startswith(('mailto:', 'tel:', 'javascript:')):
            continue
        if not re.search(r'\.html?$', urlparse(href).path, re.IGNORECASE) and '.' in Path(
            urlparse(href).path
        ).suffix:
            continue
        resolved = _normalize_url(_resolve_url(base_url, href))
        if not resolved:
            continue
        if not _is_same_domain(resolved, root_host):
            continue
        links.append(resolved)
    return links


# ---------- 主逻辑 ----------


def import_cache_to_db(outdir: Path):
    # 找 _cache.json
    cache_files = list(outdir.glob('*_cache.json'))
    if not cache_files:
        print(f'错误：在 {outdir} 中未找到 *_cache.json 文件')
        sys.exit(1)

    cache_path = cache_files[0]
    print(f'读取 manifest: {cache_path}')

    with open(cache_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    pages = manifest.get('pages', [])
    start_url = manifest.get('start_url', '')
    root_host = urlparse(start_url).netloc

    print(f'  起始 URL: {start_url}')
    print(f'  页面总数: {len(pages)}')

    # 构建 DB 路径 & 初始化
    safe_name = cache_path.stem.replace('_cache', '')
    db_path = outdir / f'{safe_name}_scrape.db'

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        'CREATE TABLE IF NOT EXISTS pages ('
        '  url TEXT PRIMARY KEY,'
        '  html_path TEXT NOT NULL,'
        '  content_type TEXT NOT NULL DEFAULT ""'
        ')'
    )
    conn.execute(
        'CREATE TABLE IF NOT EXISTS links ('
        '  url TEXT PRIMARY KEY,'
        '  links TEXT NOT NULL'
        ')'
    )
    conn.commit()

    # 1) 写入 pages 表
    print('正在写入 pages 表...')
    page_rows = [
        (p['url'], p['html_path'], p.get('content_type', ''))
        for p in pages
        if p.get('url')
    ]
    conn.executemany(
        'INSERT OR REPLACE INTO pages (url, html_path, content_type) VALUES (?, ?, ?)',
        page_rows,
    )
    conn.commit()
    print(f'  pages 写入完成: {len(page_rows)} 条')

    return
    # 2) 读取 HTML 提取链接，写入 links 表
    print('正在提取链接并写入 links 表...')
    link_count = 0
    for p in pages:
        url = p.get('url', '')
        html_path_str = p.get('html_path', '')
        if not url or not html_path_str:
            continue
        html_path = Path(html_path_str)
        if not html_path.exists():
            # 尝试相对于 outdir 拼接
            alt_path = outdir / html_path.name
            if alt_path.exists():
                html_path = alt_path
            else:
                continue

        ctype = p.get('content_type', '')
        if not HTML_CONTENT_TYPE_RE.search(ctype):
            print(f'  跳过非 HTML: {url}  ({ctype})')
            continue

        try:
            html = html_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f'  读取失败: {html_path}  {e}')
            continue

        try:
            links = _extract_links(html, url, root_host)
        except Exception as e:
            print(f'  链接提取失败: {url}  {e}')
            continue

        conn.execute(
            'INSERT OR REPLACE INTO links (url, links) VALUES (?, ?)',
            (url, json.dumps(links, ensure_ascii=False)),
        )
        link_count += 1

    conn.commit()
    conn.close()

    print(f'  links 写入完成: {link_count} 条')
    print(f'\n完成！数据库: {db_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python import_cache_to_db.py <输出目录>')
        print('示例: python import_cache_to_db.py d:/unicef')
        sys.exit(1)

    outdir = Path(sys.argv[1])
    if not outdir.is_dir():
        print(f'错误：目录不存在 {outdir}')
        sys.exit(1)

    import_cache_to_db(outdir)
