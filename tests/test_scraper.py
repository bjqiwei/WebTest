import json
import re
from pathlib import Path
import src.scraper as scraper_module

from src.scraper import (
  extract_content_blocks,
  extract_videos,
  fetch_html,
  is_file_url,
  scrape_site,
  scrape_url,
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
        """
        /download/ 模式匹配的是目录片段，可能误杀真实 HTML 页面。
        例如 /download/manual 可能是一个可访问的 HTML 文档页。
        """
        assert is_file_url('https://example.com/download/manual') is True

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


class TestExtractVideos:
    """原 extract_videos 测试。"""

    def test_extract_videos_from_html(self):
        html = '''
        <html>
          <body>
            <header><a href="/menu.mp4">menu</a></header>
            <main>
              <p>Intro paragraph for the first video.</p>
              <video src="/media/v1.mp4"></video>
              <figure>
                <video><source src="https://cdn.example.com/v2.webm"></source></video>
                <figcaption>Vid 2</figcaption>
              </figure>
              <iframe src="https://www.youtube.com/embed/abc123"></iframe>
              <a href="https://cdn.example.com/movie.mp4">download</a>
              <a href="https://cdn.example.com/movie.mp4">download duplicate</a>
            </main>
          </body>
        </html>
        '''

        videos = extract_videos(html, 'https://example.com')
        assert len(videos) == 4
        assert videos[0]['original_url'].endswith('/media/v1.mp4')
        assert videos[1]['original_url'] == 'https://cdn.example.com/v2.webm'
        assert videos[1]['note'] == 'Vid 2'
        assert videos[2]['original_url'] == 'https://www.youtube.com/embed/abc123'
        assert videos[3]['original_url'] == 'https://cdn.example.com/movie.mp4'
        assert [v['index'] for v in videos] == [1, 2, 3, 4]


def test_scrape_url_outputs_counter_and_json(tmp_path, monkeypatch):
    sample_html = '''
    <html>
      <body>
        <main>
          <p>Video description text around content area.</p>
          <video src="https://cdn.example.com/a.mp4"></video>
        </main>
      </body>
    </html>
    '''

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': sample_html, 'content_type': 'text/html'})

    result = scrape_url('https://www.bosch.com/careers', tmp_path)

    assert result['page_count'] == 1
    assert result['video_count'] == 1
    assert result['media_count'] == 1

    assert re.search(r'0001_careers_html_\d{14}\.json$', result['json_path'])
    json_path = tmp_path / Path(result['json_path']).name
    data = json.loads(json_path.read_text(encoding='utf-8'))
    assert data['original_link'] == 'https://www.bosch.com/careers'
    assert 'content_blocks' in data
    assert data['extra'] == {}
    media_items = [b for b in data['content_blocks'] if isinstance(b, dict)]
    assert media_items[0]['original_url'] == 'https://cdn.example.com/a.mp4'
    assert media_items[0]['type'] == 'video'


def test_scrape_url_decodes_output_filename(tmp_path, monkeypatch):
    sample_html = '<html><body><main><p>ok content</p></main></body></html>'

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': sample_html, 'content_type': 'text/html'})

    result = scrape_url('https://example.com/hello%20world', tmp_path)
    output_name = Path(result['json_path']).name

    assert 'hello world' in output_name


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
    assert media_items[0]['index'] == 1
    assert media_items[1]['type'] == 'video'
    assert media_items[1]['index'] == 2


def test_extract_content_blocks_excludes_related_section():
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
    assert 'Related' not in blocks
    assert 'Related article' not in blocks


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
    assert result['media_count'] == 2
    assert result['failed_count'] == 0

    first = result['pages'][0]
    second = result['pages'][1]
    assert first['url'] == 'https://example.com/'
    assert second['url'] == 'https://example.com/careers.html'

    summary = json.loads((tmp_path / 'example.com_summary.json').read_text(encoding='utf-8'))
    assert summary['page_count'] == 2
    assert summary['video_count'] == 2
    assert summary['media_count'] == 2


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
    assert result['page_count'] == 2
    assert result['failed_count'] == 0
    assert [p['url'] for p in result['pages']] == ['https://example.com/', 'https://example.com/about']


def test_scrape_site_unlimited_depth_and_pages(tmp_path, monkeypatch):
    pages = {
    'https://example.com/': '<html><body><a href="/a.html">a</a></body></html>',
    'https://example.com/a.html': '<html><body><a href="/b.html">b</a></body></html>',
    'https://example.com/b.html': '<html><body><p>end</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    result = scrape_site('https://example.com', tmp_path, max_depth=-1, max_pages=0)
    assert result['page_count'] == 3


def test_scrape_site_skip_parse_error_and_continue(tmp_path, monkeypatch):
    pages = {
  'https://example.com/': '<html><body><a href="/bad.html">bad</a><a href="/good.html">good</a></body></html>',
  'https://example.com/bad.html': '<html><body><main>bad page</main></body></html>',
  'https://example.com/good.html': '<html><body><main><p>ok</p></main></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    original_save = scraper_module._save_page_output

    def flaky_save(url, html, outdir, page_index, timestamp):
        if url.endswith('/bad.html'):
            raise ValueError('parse error')
        return original_save(url, html, outdir, page_index, timestamp)

    monkeypatch.setattr('src.scraper._save_page_output', flaky_save)

    result = scrape_site('https://example.com', tmp_path, max_depth=1, max_pages=10)
    assert result['page_count'] == 2
    assert result['failed_count'] == 1
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
        text = '<html><title>Just a moment...</title></html>'
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
      <script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
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

    assert result['page_count'] == 3
    assert [p[0] for p in progress] == [1, 2, 3]
    assert [p[1] for p in progress] == [3, 3, 3]


def test_scrape_site_saves_non_html_content_without_extension(monkeypatch, tmp_path):
    pages = {
        'https://example.com/': '<html><body><a href="/img/noext">img</a><a href="/ok.html">ok</a></body></html>',
        'https://example.com/img/noext': 'JFIF_BINARY_BYTES',
        'https://example.com/ok.html': '<!doctype html><html><body><p>ok</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'image/jpeg' if 'noext' in url else 'text/html'})

    # Use save_site_html directly to see save-step results (including failures)
    result = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)
    # All 3 pages saved (non-HTML content no longer rejected)
    assert result['saved_count'] == 3
    assert result['failed_count'] == 0
    # Cache should contain content_type field
    cache_files = list(tmp_path.glob('*_cache.json'))
    assert len(cache_files) == 1
    cache_payload = json.loads(cache_files[0].read_text(encoding='utf-8'))
    noext_entry = [p for p in cache_payload['pages'] if 'noext' in p['url']][0]
    assert noext_entry['content_type'] == 'image/jpeg'
    ok_entry = [p for p in cache_payload['pages'] if 'ok' in p['url']][0]
    assert ok_entry['content_type'] == 'text/html'


def test_save_site_html_reuses_cached_local_html(monkeypatch, tmp_path):
    pages = {
        'https://example.com/': '<html><body><a href="/a.html">a</a></body></html>',
        'https://example.com/a.html': '<html><body><p>a</p></body></html>',
    }

    fetch_calls = {'count': 0}

    def fake_fetch(url, **kwargs):
        fetch_calls['count'] += 1
        return {'html': pages[url], 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    first = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)
    assert first['saved_count'] == 2
    assert fetch_calls['count'] == 2

    cache_files = list(tmp_path.glob('*_cache.json'))
    assert len(cache_files) == 1
    cache_payload = json.loads(cache_files[0].read_text(encoding='utf-8'))
    assert cache_payload['saved_count'] == 2
    assert {p['url'] for p in cache_payload['pages']} == {'https://example.com/', 'https://example.com/a.html'}
    # Verify content_type is present in cache entries
    for p in cache_payload['pages']:
        assert p.get('content_type') == 'text/html'

    def fail_fetch(url, **kwargs):
        raise AssertionError(f'fetch_html should not be called for cached URL: {url}')

    monkeypatch.setattr('src.scraper.fetch_html', fail_fetch)

    second = scraper_module.save_site_html('https://example.com', tmp_path, max_depth=1, max_pages=10)
    assert second['saved_count'] == 2
    assert second['failed_count'] == 0

    cache_payload = json.loads(cache_files[0].read_text(encoding='utf-8'))
    assert cache_payload['saved_count'] == 2
    assert cache_payload['pages'][0]['url'] == 'https://example.com/'
    assert cache_payload['pages'][1]['url'] == 'https://example.com/a.html'
