import json
import re
from pathlib import Path
import src.scraper as scraper_module

from src.scraper import (
  extract_content_blocks,
  fetch_html,
  is_file_url,
  scrape_site,
)


class TestIsFileUrl:
    """单元测试 is_file_url 的三个过滤分支。"""

    # ── 分支 1：显式扩展名 ──

    def test_known_extension_pdf(self):
        assert is_file_url('https://example.com/doc.pdf') is True

    def test_known_extension_mp4(self):
        assert is_file_url('https://example.com/video.mp4') is True

    def test_known_extension_jpg(self):
        assert is_file_url('https://example.com/photo.jpg') is True

    def test_known_extension_zip(self):
        assert is_file_url('https://example.com/archive.zip') is True

    def test_known_extension_case_insensitive(self):
        assert is_file_url('https://example.com/doc.PDF') is True
        assert is_file_url('https://example.com/photo.JPEG') is True

    # ── 分支 2：路径模式 ──

    def test_unicef_media_pattern(self):
        assert is_file_url('https://www.unicef.org/media/7606/file') is True

    def test_unicef_media_pattern_with_query(self):
        assert is_file_url('https://www.unicef.org/media/7606/file?download=1') is True

    def test_download_path_pattern(self):
        assert is_file_url('https://example.com/download/setup.exe') is True

    def test_assets_with_dot_pattern(self):
        assert is_file_url('https://example.com/assets/css/style.css') is True

    def test_static_with_dot_pattern(self):
        assert is_file_url('https://example.com/static/js/app.js') is True

    # ── 分支 3：泛化后缀检查（非 .html/.htm）──

    def test_non_html_suffix_bak(self):
        assert is_file_url('https://example.com/backup.zip.bak') is True

    def test_non_html_suffix_shtml(self):
        assert is_file_url('https://example.com/page.shtml') is True

    def test_non_html_suffix_php(self):
        assert is_file_url('https://example.com/index.php') is True

    # ── 否定用例 ──

    def test_plain_html(self):
        assert is_file_url('https://example.com/about.html') is False

    def test_plain_htm(self):
        assert is_file_url('https://example.com/index.htm') is False

    def test_extensionless_route(self):
        assert is_file_url('https://example.com/about') is False

    def test_root_url(self):
        assert is_file_url('https://example.com/') is False

    def test_deep_extensionless_route(self):
        assert is_file_url('https://example.com/careers/jobs') is False

    # ── 回归风险：目录型路由不应被误判 ──

    def test_download_directory_html_route(self):
        """目录型路由不必判为文件（/download/ 模式已移除）。"""
        assert is_file_url('https://example.com/download/manual') is False

    def test_assets_directory_no_dot(self):
        """没有 '.' 的 /assets/ 路径不应被误判为文件。"""
        assert is_file_url('https://example.com/assets/brand') is False

    def test_static_directory_no_dot(self):
        """没有 '.' 的 /static/ 路径不应被误判为文件。"""
        assert is_file_url('https://example.com/static/about') is False

    def test_media_non_file_route(self):
        """/media/ 后跟的不是 file 端点不应被误判。"""
        assert is_file_url('https://example.com/media/7606') is False
        assert is_file_url('https://example.com/media/news') is False


def test_safe_name_root_url_has_single_index():
    assert scraper_module._safe_name_from_url('https://www.unicef.org/') == 'www.unicef.org'


def test_extract_content_blocks_with_image_and_video():
    html = '''
    <html>
      <body>
        <main>
          <h1>Careers</h1>
          <p>Work with us</p>
          <img src="/hero.webp" alt="Hero banner" />
          <video src="/intro.mp4"></video>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com/careers')
    assert 'Careers' in blocks
    assert 'Work with us' in blocks
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert len(media_items) == 2
    assert media_items[0]['type'] == 'image'
    assert media_items[0]['alt'] == 'Hero banner'
    assert media_items[0]['index'] == 1  # image 独立序号
    assert media_items[1]['type'] == 'video'
    assert media_items[1]['index'] == 1  # video 独立序号


def test_extract_content_blocks_keeps_related_section():
    """'related' 已移出 NOISE_KEYWORDS：相关阅读区域作为有效内容保留。"""
    html = '''
    <html>
      <body>
        <main>
          <h1>Main title</h1>
          <p>Main content</p>
          <section class="related">
            <h2>Related</h2>
            <p>Related article</p>
          </section>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com/page')
    assert 'Main title' in blocks
    assert 'Main content' in blocks
    assert 'Related' in blocks
    assert 'Related article' in blocks


def test_extract_content_blocks_not_blocked_by_body_navigation_class():
    html = '''
    <html>
      <body class="secondary-navigation-dropdown-style-black-outlined">
        <main>
          <h1>UNICEF headline</h1>
          <p>Children first content.</p>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://www.unicef.org/')
    assert 'UNICEF headline' in blocks
    assert 'Children first content.' in blocks


def test_extract_content_blocks_allows_images_inside_form():
    html = '''
    <html>
      <body>
        <main>
          <form>
            <img src="/inside-form.webp" alt="Inside form" />
          </form>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert len(media_items) == 1
    assert media_items[0]['type'] == 'image'
    assert media_items[0]['original_url'] == 'https://example.com/inside-form.webp'


def test_extract_content_blocks_skips_base64_image():
    """base64（data URI）形式的图片应被忽略，普通图片仍正常提取。"""
    html = '''
    <html>
      <body>
        <main>
          <h1>Title</h1>
          <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==" alt="inline base64" />
          <img src="data:image/svg+xml;base64,PHN2Zy8+" alt="inline svg" />
          <img src="/hero.webp" alt="Hero" />
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert len(media_items) == 1, 'base64 图片应被忽略，仅保留普通图片'
    assert media_items[0]['type'] == 'image'
    assert media_items[0]['original_url'] == 'https://example.com/hero.webp'


def test_extract_content_blocks_skips_base64_image_from_data_src():
    """img 的 data-src 为 base64 时应同样忽略。"""
    html = '''
    <html>
      <body>
        <main>
          <img data-src="data:image/webp;base64,UklGRi4A" alt="lazy base64" />
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert media_items == [], 'data-src 为 base64 的图片不应被提取'


def test_extract_content_blocks_truncates_after_cutoff_marker():
    """命中截断标记后，其后的内容块全部忽略，标记本身的内容（视频）保留。

    显式传入 cutoff_markers 测试截断机制（全局 CONTENT_CUTOFF_MARKERS 现为空）。
    """
    html = '''
    <html>
      <body>
        <main>
          <h1>Careers</h1>
          <p>Work with us</p>
          <figure>
            <iframe src="https://www.youtube-nocookie.com/embed/5r3gIPTuaik"></iframe>
            <figcaption><span class="note m-credit">UNICEF Division of Human Resources</span></figcaption>
          </figure>
          <h2>Noticias y testimonios de profesionales</h2>
          <p>This content should be ignored.</p>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(
        html, 'https://www.unicef.org/careers/es',
        cutoff_markers=('Noticias y testimonios de profesionales',),
    )
    texts = [b for b in blocks if isinstance(b, str)]
    assert 'Careers' in texts
    assert 'Work with us' in texts
    assert 'Noticias y testimonios de profesionales' not in texts
    assert 'This content should be ignored.' not in texts
    videos = [b for b in blocks if isinstance(b, dict) and b['type'] == 'video']
    assert len(videos) == 1
    assert videos[0]['note'] == 'UNICEF Division of Human Resources'


def test_extract_content_blocks_cutoff_can_be_disabled():
    """传入 cutoff_markers=() 时关闭截断，标记之后的内容仍被保留。"""
    html = '''
    <html>
      <body>
        <main>
          <h1>Careers</h1>
          <p>UNICEF Division of Human Resources</p>
          <h2>After marker</h2>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com', cutoff_markers=())
    assert 'After marker' in blocks


def test_extract_content_blocks_no_marker_no_truncation():
    """页面不含截断标记时，行为与之前一致（全部保留）。"""
    html = '''
    <html>
      <body>
        <main>
          <h1>Careers</h1>
          <p>Everything should be kept</p>
          <h2>Second section</h2>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    texts = [b for b in blocks if isinstance(b, str)]
    assert 'Everything should be kept' in texts
    assert 'Second section' in texts


def test_extract_content_blocks_cutoff_ignores_marker_in_noise_area():
    """截断标记出现在导航/页眉等噪音区域时不触发截断，只有正文中的标记才触发。"""
    html = '''
    <html>
      <body>
        <header>
          <nav><ul><li><a href="/novedades"><span>Novedades</span></a></li></ul></nav>
        </header>
        <main>
          <h1>Title</h1>
          <iframe src="https://www.youtube-nocookie.com/embed/abc123"></iframe>
          <h2>Novedades</h2>
          <p>This is the news section, should be cut.</p>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com', cutoff_markers=('Novedades',))
    texts = [b for b in blocks if isinstance(b, str)]
    # 导航菜单里的 "Novedades" 不应触发截断：正文与视频保留
    assert 'Title' in texts
    videos = [b for b in blocks if isinstance(b, dict) and b['type'] == 'video']
    assert len(videos) == 1
    # 正文中的 "Novedades" 标题之后的新闻内容被截断
    assert 'This is the news section, should be cut.' not in texts


def test_extract_content_blocks_skips_button_content():
    """按钮内容（<button> 或带 btn/cta-button class 的 <a>）不应提取。"""
    html = '''
    <html>
      <body>
        <main>
          <h1>Title</h1>
          <p>Real paragraph content.</p>
          <a href="https://help.example.org/dona" class="btn btn-donate cta-button">Doná ahora</a>
          <p>Another paragraph.</p>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.org')
    texts = [b for b in blocks if isinstance(b, str)]
    assert 'Real paragraph content.' in texts
    assert 'Another paragraph.' in texts
    assert not any('Doná ahora' in t for t in texts), '按钮文本不应被提取'


def test_extract_content_blocks_skips_404_link_page():
    """页面 <link> 元素 href 含 page-404 时，不提取该页面的视频、图片等内容。"""
    html = '''
    <html>
      <head>
        <link rel="canonical" href="https://example.com/fr/page-404">
      </head>
      <body>
        <main>
          <h1>Page not found</h1>
          <img src="/hero.webp" alt="Hero" />
          <video src="/intro.mp4"></video>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com/fr')
    assert blocks == [], '404 页面不应提取任何内容'


def test_extract_content_blocks_skips_error_404_link_page():
    """error-404 标记同样生效（如西语错误页）。"""
    html = '''
    <html>
      <head>
        <link rel="canonical" href="https://example.com/es/error-404">
      </head>
      <body>
        <main>
          <video src="/intro.mp4"></video>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com/es')
    assert blocks == []


def test_extract_content_blocks_keeps_normal_link_pages():
    """正常页面即使有 <link> 元素（不含 404 标记），视频和图片仍正常提取。"""
    html = '''
    <html>
      <head>
        <link rel="canonical" href="https://example.com/careers">
      </head>
      <body>
        <main>
          <h1>Careers</h1>
          <img src="/hero.webp" alt="Hero" />
          <video src="/intro.mp4"></video>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    assert 'Careers' in blocks
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert len(media_items) == 2


def test_scrape_site_recursive_same_domain(tmp_path, monkeypatch):
    pages = {
        'https://example.com/': '''
        <html><body><main>
          <a href="/careers.html">careers</a>
          <a href="/about.css">about-style</a>
          <a href="/brochure.pdf">brochure</a>
          <a href="https://other.com/page">external</a>
          <video src="/v0.mp4"></video>
        </main></body></html>
      ''',
        'https://example.com/careers.html': '''
        <html><body><main>
          <iframe src="https://www.youtube.com/embed/abc"></iframe>
        </main></body></html>
      ''',
    }

    def fake_fetch(url, **kwargs):
        return {'html': pages[url], 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    result = scrape_site('https://example.com', tmp_path, max_depth=1, max_pages=10)

    assert result['page_count'] == 2
    assert result['video_count'] == 2
    assert result['page_count'] == 2
    assert result['video_count'] == 2
    assert result['image_count'] == 0
    assert result['failed_count'] == 0


def test_scrape_site_follows_header_nav_links(tmp_path, monkeypatch):
    pages = {
        'https://example.com/': '''
        <html><body>
          <header>
            <nav><a href="/about">About</a></nav>
          </header>
          <main><p>Home</p></main>
        </body></html>
        ''',
        'https://example.com/about': '<html><body><main><p>About us</p></main></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    result = scrape_site('https://example.com', tmp_path, max_depth=1, max_pages=10)
    # page_count 只统计含视频的页面；两页均无视频，用 saved_count 验证抓取数量
    assert result['saved_count'] == 2
    assert result['failed_count'] == 0


def test_scrape_site_unlimited_depth_and_pages(tmp_path, monkeypatch):
    pages = {
    'https://example.com/': '<html><body><a href="/a.html">a</a></body></html>',
    'https://example.com/a.html': '<html><body><a href="/b.html">b</a></body></html>',
    'https://example.com/b.html': '<html><body><p>end</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    result = scrape_site('https://example.com', tmp_path, max_depth=-1, max_pages=0)
    # 页面均无视频，page_count 为 0；用 saved_count 验证全部抓取
    assert result['saved_count'] == 3


def test_scrape_site_skip_parse_error_and_continue(tmp_path, monkeypatch):
    pages = {
  'https://example.com/': '<html><body><a href="/bad.html">bad</a><a href="/good.html">good</a></body></html>',
  'https://example.com/bad.html': '<html><body><main>bad page<video src="/bad.mp4"></video></main></body></html>',
  'https://example.com/good.html': '<html><body><main><p>ok</p><video src="/ok.mp4"></video></main></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    original_save = scraper_module._save_page_output

    def flaky_save(url, html, outdir, page_index, timestamp, **kwargs):
        if url.endswith('/bad.html'):
            raise ValueError('parse error')
        return original_save(url, html, outdir, page_index, timestamp, **kwargs)

    monkeypatch.setattr('src.scraper._save_page_output', flaky_save)

    result = scrape_site('https://example.com', tmp_path, max_depth=1, max_pages=10)
    # 仅 good.html 有视频且保存成功 → page_count=1；bad.html 保存失败记入 failed
    assert result['page_count'] == 1
    assert result['failed_count'] == 1  # bad.html 有视频但解析失败
    assert 'https://example.com/bad.html' in result['failed']


def test_scrape_site_persists_failed_fetch_pages(tmp_path, monkeypatch):
    pages = {
      'https://example.com/': '<html><body><a href="/bad.html">bad</a><a href="/good.html">good</a></body></html>',
      'https://example.com/good.html': '<html><body><main><p>ok</p></main></body></html>',
    }

    def fake_fetch(url, **kwargs):
      if url.endswith('/bad.html'):
        raise RuntimeError('network down')
      return {'html': pages[url], 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    # save_site_html returns the save-step result which includes fetch failures
    result = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)

    assert result['failed_count'] == 1
    # 从 SQLite 查询失败页面记录
    import sqlite3
    from src.scraper import _scrape_db_path
    db_path = _scrape_db_path('https://example.com', tmp_path)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT url, reason FROM failed_pages")
    rows = list(cursor)
    conn.close()
    assert len(rows) == 1
    failed_url, failed_reason = rows[0]
    assert failed_url == 'https://example.com/bad.html'
    assert 'network down' in failed_reason


def test_fetch_html_auto_fallback_to_playwright(monkeypatch):
    class DummyResp:
        status_code = 200
        encoding = 'utf-8'
        apparent_encoding = 'utf-8'
        # 包含当前 strong_markers 之一（checking your browser）以触发 Playwright 回退
        text = '<html><title>Just a moment...</title><body>checking your browser</body></html>'
        headers = {'content-type': 'text/html; charset=utf-8'}

        def raise_for_status(self):
            return None

    monkeypatch.setattr('src.scraper.requests.get', lambda *args, **kwargs: DummyResp())
    monkeypatch.setattr('src.scraper.fetch_html_with_playwright', lambda *args, **kwargs: {'html': '<html><main>ok</main></html>', 'content_type': 'text/html'})

    result = fetch_html('https://example.com', renderer='auto')
    assert '<main>ok</main>' in result['html']


def test_challenge_detector_not_triggered_by_generic_cloudflare_text():
    html = '''
    <html><body>
      <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
      <p>normal page</p>
    </body></html>
    '''
    assert scraper_module._is_challenge_or_block_page(html) is False


def test_challenge_detector_triggered_by_cloudflare_challenge_markers():
    html = '''
    <html><head><title>Just a moment...</title></head><body>
      <meta name="cf-browser-verification" content="abc">
      <script src="/cdn-cgi/browser-verification"></script>
    </body></html>
    '''
    assert scraper_module._is_challenge_or_block_page(html) is True


def test_fetch_html_playwright_mode(monkeypatch):
    monkeypatch.setattr('src.scraper.fetch_html_with_playwright', lambda *args, **kwargs: {'html': '<html>pw</html>', 'content_type': 'text/html'})
    result = fetch_html('https://example.com', renderer='playwright')
    assert 'pw' in result['html']


def test_fetch_html_blocked_after_playwright(monkeypatch):
    class DummyResp:
        status_code = 403
        encoding = 'utf-8'
        apparent_encoding = 'utf-8'
        text = '<html><title>Just a moment...</title></html>'
        headers = {'content-type': 'text/html; charset=utf-8'}

        def raise_for_status(self):
            raise requests.HTTPError('403')

    import requests

    monkeypatch.setattr('src.scraper.requests.get', lambda *args, **kwargs: DummyResp())
    monkeypatch.setattr('src.scraper.fetch_html_with_playwright', lambda *args, **kwargs: {'html': '<html><title>Just a moment...</title></html>', 'content_type': 'text/html'})

    result = fetch_html('https://example.com', renderer='auto')
    # After requests 403 fallback, playwright returns a challenge page.
    # The challenge page content is preserved (not blocked as error).
    assert 'Just a moment' in result['html']


def test_scrape_site_progress_uses_fixed_total(monkeypatch, tmp_path):
    pages = {
        'https://example.com/': '<html><body><a href="/a.html">a</a></body></html>',
        'https://example.com/a.html': '<html><body><a href="/b.html">b</a></body></html>',
        'https://example.com/b.html': '<html><body><p>end</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    progress = []

    def callback(done, total, url):
        progress.append((done, total, url))

    result = scrape_site(
        'https://example.com',
        tmp_path,
        max_depth=-1,
        max_pages=0,
        progress_callback=callback,
    )

    assert result['saved_count'] == 3
    # analyze 阶段并发处理，完成顺序不确定，只校验序号集合与固定总数
    assert sorted(p[0] for p in progress) == [1, 2, 3]
    assert [p[1] for p in progress] == [3, 3, 3]


def test_scrape_site_marks_non_html_content_as_failed(monkeypatch, tmp_path):
    """非 HTML 内容类型（无扩展名资源）不保存为 HTML，而是记入 failed（reason=not_html_content）。"""
    pages = {
        'https://example.com/': '<html><body><a href="/img/noext">img</a><a href="/ok.html">ok</a></body></html>',
        'https://example.com/img/noext': 'JFIF_BINARY_BYTES',
        'https://example.com/ok.html': '<!doctype html><html><body><p>ok</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'image/jpeg' if 'noext' in url else 'text/html'})

    # /img/noext 路径为 2 段（depth=2），需 max_depth=2 才会被抓取
    result = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=2, max_pages=10)
    # 仅 HTML 页面被保存；非 HTML 内容记入 failed
    assert result['saved_count'] == 2
    assert result['failed_count'] == 1
    import sqlite3
    from src.scraper import _scrape_db_path
    db = _scrape_db_path('https://example.com', tmp_path)
    conn = sqlite3.connect(str(db))
    pages_rows = dict(conn.execute("SELECT url, content_type FROM pages"))
    failed_rows = dict(conn.execute("SELECT url, reason FROM failed_pages"))
    conn.close()
    assert 'https://example.com/img/noext' not in pages_rows
    assert pages_rows.get('https://example.com/ok.html') == 'text/html'
    assert failed_rows.get('https://example.com/img/noext') == 'not_html_content'


def test_save_site_html_reuses_cached_local_html(monkeypatch, tmp_path):
    pages = {
        'https://example.com/': '<html><body><a href="/a.html">a</a></body></html>',
        'https://example.com/a.html': '<html><body><p>a</p></body></html>',
    }

    def _db_urls():
        import sqlite3
        from src.scraper import _scrape_db_path
        db = _scrape_db_path('https://example.com', tmp_path)
        if not db.exists():
            return []
        conn = sqlite3.connect(str(db))
        rows = [row[0] for row in conn.execute("SELECT url FROM pages ORDER BY url")]
        conn.close()
        return rows

    fetch_calls = {'count': 0}

    def fake_fetch(url, **kwargs):
        fetch_calls['count'] += 1
        return {'html': pages[url], 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    first = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)
    assert first['saved_count'] == 2
    assert fetch_calls['count'] == 2
    assert set(_db_urls()) == {'https://example.com/', 'https://example.com/a.html'}

    def fail_fetch(url, **kwargs):
        raise AssertionError(f'fetch_html should not be called for cached URL: {url}')

    monkeypatch.setattr('src.scraper.fetch_html', fail_fetch)

    second = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)
    assert second['saved_count'] == 2
    assert second['failed_count'] == 0
    assert set(_db_urls()) == {'https://example.com/', 'https://example.com/a.html'}


def _seed_analyze_pages(tmp_path):
    """构造两个已保存页面（一个含视频、一个无视频）并写入 SQLite（video_count=-1）。"""
    import sqlite3
    from src.scraper import _scrape_db_path, _init_db

    start_url = 'https://example.com'
    video_html = '''<html><body><main>
      <h1>With video</h1>
      <iframe src="https://www.youtube-nocookie.com/embed/5r3gIPTuaik"></iframe>
    </main></body></html>'''
    novideo_html = '<html><body><main><h1>No video</h1><p>text only</p></main></body></html>'

    html_dir = tmp_path / 'pages'
    html_dir.mkdir(exist_ok=True)
    video_file = html_dir / 'video_page.html'
    novideo_file = html_dir / 'novideo_page.html'
    video_file.write_text(video_html, encoding='utf-8')
    novideo_file.write_text(novideo_html, encoding='utf-8')

    db_path = _scrape_db_path(start_url, tmp_path)
    conn = _init_db(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO pages (url, html_path, content_type, video_count, image_count)"
        " VALUES (?, ?, ?, -1, -1)",
        [
            ('https://example.com/video', str(video_file), 'text/html'),
            ('https://example.com/novideo', str(novideo_file), 'text/html'),
        ],
    )
    conn.commit()
    conn.close()
    return start_url, db_path, video_file, novideo_file


def test_analyze_delete_html_no_video_keeps_db_records(tmp_path):
    """delete_html_no_video=True：删除无视频页面的本地 HTML，但 DB 记录保留。"""
    import sqlite3

    start_url, db_path, video_file, novideo_file = _seed_analyze_pages(tmp_path)

    result = scraper_module.analyze_saved_html(start_url, tmp_path, delete_html_no_video=True)

    assert result['deleted_html_count'] == 1
    assert video_file.exists(), '含视频页面的 HTML 不应被删除'
    assert not novideo_file.exists(), '无视频页面的 HTML 应被删除'

    conn = sqlite3.connect(str(db_path))
    rows = dict(conn.execute("SELECT url, video_count FROM pages"))
    conn.close()
    assert set(rows) == {'https://example.com/video', 'https://example.com/novideo'}
    assert rows['https://example.com/video'] == 1
    assert rows['https://example.com/novideo'] == 0


def test_analyze_keeps_html_by_default(tmp_path):
    """默认（delete_html_no_video=False）不删除任何本地 HTML。"""
    start_url, db_path, video_file, novideo_file = _seed_analyze_pages(tmp_path)

    result = scraper_module.analyze_saved_html(start_url, tmp_path)

    assert result['deleted_html_count'] == 0
    assert video_file.exists()
    assert novideo_file.exists(), '默认不应删除无视频页面的 HTML'


def test_save_skips_no_video_pages_with_deleted_html(monkeypatch, tmp_path):
    """video_count=0 的页面即使本地 HTML 已被删除，save 时也不会重新下载。"""
    import sqlite3
    from src.scraper import _scrape_db_path, _init_db

    start_url = 'https://example.com'
    html_dir = tmp_path / 'pages'
    html_dir.mkdir(exist_ok=True)
    page_a_file = html_dir / 'a.html'
    page_a_file.write_text('<html><body><a href="/b">to B</a></body></html>', encoding='utf-8')

    db_path = _scrape_db_path(start_url, tmp_path)
    conn = _init_db(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO pages (url, html_path, content_type, video_count, image_count)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            # A 页面本地文件存在，链接到 B；尚未分析（video_count=-1）
            ('https://example.com/', str(page_a_file), 'text/html', -1, -1),
            # B 页面已分析为无视频（video_count=0），本地 HTML 已被删除
            ('https://example.com/b', str(html_dir / 'b_gone.html'), 'text/html', 0, 0),
        ],
    )
    conn.commit()
    conn.close()

    fetched = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        return {'html': '<html><body><p>x</p></body></html>', 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    scraper_module.save_site_html(start_url, tmp_path, max_depth=1, max_pages=10)

    # B 不应被重新下载（video_count=0 代表无需再下载）
    assert 'https://example.com/b' not in fetched


def test_save_excludes_too_many_requests_pages(monkeypatch, tmp_path):
    """title 为 "Too Many Requests" 的页面应被排除：不保存为有效 HTML，记入 failed。"""
    pages = {
        'https://example.com/': '<html><body><a href="/ok.html">ok</a><a href="/429.html">429</a></body></html>',
        'https://example.com/ok.html': '<!doctype html><html><body><p>ok content</p></body></html>',
        'https://example.com/429.html': '<html><head><title>Too Many Requests</title></head><body>rate limited</body></html>',
    }

    monkeypatch.setattr(
        'src.scraper.fetch_html',
        lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'},
    )

    result = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)

    # 根页面 + ok.html 被保存；429 页面被排除
    assert result['saved_count'] == 2
    assert result['failed_count'] == 1

    # 429 页面不应作为有效 HTML 保存到输出目录根
    saved_names = [p.name for p in tmp_path.glob('*.html')]
    assert not any('429' in n for n in saved_names)

    # failed 表中记录 reason = challenge_or_block（429 已并入挑战/封锁页检测）
    import sqlite3
    from src.scraper import _scrape_db_path
    db = _scrape_db_path('https://example.com', tmp_path)
    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT url, reason FROM failed_pages"))
    conn.close()
    assert rows.get('https://example.com/429.html') == 'challenge_or_block'
