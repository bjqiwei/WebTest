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
    assert scraper_module._safe_name_from_url('https://www.unicef.org/') == 'unicef.org'


def test_url_depth_is_derived_from_path_segments_only():
    assert scraper_module._url_depth('https://example.com') == 0
    assert scraper_module._url_depth('https://example.com/') == 0
    assert scraper_module._url_depth('https://example.com/about') == 1
    assert scraper_module._url_depth('https://example.com/a/b/c') == 3


def test_is_404_page_uses_title_only():
    """正文里出现 404 词不应触发 404 判定，必须看 title。"""
    html = '''
    <html>
      <head><title>Home</title></head>
      <body>
        <h1>404 Not Found</h1>
      </body>
    </html>
    '''
    assert scraper_module._is_404_page(scraper_module.BeautifulSoup(html, 'html.parser')) is False

    html = '''
    <html>
      <head><title>Error 404 | Panthera</title></head>
      <body><p>Some content</p></body>
    </html>
    '''
    assert scraper_module._is_404_page(scraper_module.BeautifulSoup(html, 'html.parser')) is True


def test_is_same_domain_ignores_www_prefix():
    """_is_same_domain 应忽略 root_host 的 www. 前缀，子域名视为同域。"""
    # root 带 www 时，裸域名与子域名都算同域
    assert scraper_module._is_same_domain('https://www.panthera.org/x', 'www.panthera.org') is True
    assert scraper_module._is_same_domain('https://panthera.org/x', 'www.panthera.org') is True
    assert scraper_module._is_same_domain('https://store.panthera.org/x', 'www.panthera.org') is True
    # root 不带 www 时行为不变
    assert scraper_module._is_same_domain('https://store.panthera.org/x', 'panthera.org') is True
    assert scraper_module._is_same_domain('https://www.panthera.org/x', 'panthera.org') is True
    # 外部域名仍被排除
    assert scraper_module._is_same_domain('https://other.org/x', 'www.panthera.org') is False


def test_safe_name_strips_www_prefix():
    """数据库名（_safe_name_from_url）只保留 host，并去掉 host 的 www. 前缀。"""
    assert scraper_module._safe_name_from_url('https://www.panthera.org/cat') == 'panthera.org'
    assert scraper_module._safe_name_from_url('https://panthera.org/cat') == 'panthera.org'
    assert scraper_module._safe_name_from_url('https://panthera.org/') == 'panthera.org'
    # 只去掉 www. 前缀，不改变剩余 host 的大小写
    assert scraper_module._safe_name_from_url('https://WWW.Example.com') == 'Example.com'


class TestNormalizeUrl:
    """单元测试 URL 归一化：去 fragment/query、去尾部斜杠、补 www。"""

    def test_trailing_slash_collapses(self):
        assert scraper_module._normalize_url('https://www.panthera.org/cat/small-cats') == \
               scraper_module._normalize_url('https://www.panthera.org/cat/small-cats/')

    def test_trailing_slash_removed(self):
        assert scraper_module._normalize_url('https://panthera.org/cat/small-cats/') == \
               'https://panthera.org/cat/small-cats'

    def test_root_slash_removed(self):
        # 根路径统一规范为无斜杠形式
        assert scraper_module._normalize_url('https://panthera.org') == 'https://panthera.org'
        assert scraper_module._normalize_url('https://panthera.org/') == 'https://panthera.org'
        assert scraper_module._normalize_url('https://panthera.org/') == \
               scraper_module._normalize_url('https://panthera.org')

    def test_query_and_fragment_stripped(self):
        assert scraper_module._normalize_url('https://panthera.org/cat/small-cats/?utm=1#top') == \
               'https://panthera.org/cat/small-cats'

    def test_apex_www_not_added(self):
        # _normalize_url 不再补 www 前缀（www/裸域名的去重交给 _remove_scheme）
        assert scraper_module._normalize_url('https://panthera.org') == 'https://panthera.org'
        assert scraper_module._normalize_url('https://panthera.org/cat') == 'https://panthera.org/cat'

    def test_existing_www_untouched(self):
        assert scraper_module._normalize_url('https://www.panthera.org/cat') == 'https://www.panthera.org/cat'

    def test_subdomain_untouched(self):
        # 子域名（store.xxx、go.xxx）不应被补 www
        assert scraper_module._normalize_url('https://store.panthera.org/cat') == 'https://store.panthera.org/cat'
        assert scraper_module._normalize_url('https://go.panthera.org') == 'https://go.panthera.org'

    def test_remove_scheme_ignores_scheme(self):
        # 去重 key 忽略 http/https scheme（_normalize_url 本身不再统一 scheme）
        assert scraper_module._remove_scheme('http://www.panthera.org/blog-post/x') == \
               scraper_module._remove_scheme('https://www.panthera.org/blog-post/x')
        assert scraper_module._remove_scheme('https://www.panthera.org/x') == 'panthera.org/x'

    def test_remove_scheme_strips_www_prefix(self):
        # www. 与裸域名折叠为同一去重 key；路径里的 www 不受影响
        assert scraper_module._remove_scheme('https://www.panthera.org/x') == \
               scraper_module._remove_scheme('https://panthera.org/x')
        assert scraper_module._remove_scheme('http://panthera.org/') == 'panthera.org'
        assert scraper_module._remove_scheme('https://panthera.org/www/foo') == 'panthera.org/www/foo'


def test_extract_links_normalizes_trailing_slash():
    html = '''
    <html><body>
      <a href="/cat/small-cats">A</a>
      <a href="/cat/small-cats/">B</a>
      <a href="/cat/small-cats/#frag">C</a>
    </body></html>
    '''
    links = scraper_module._extract_links(html, 'https://panthera.org/', 'panthera.org')
    # 三个不同写法应被归一化为同一个 URL（去重由爬取循环的 queued/visited 集合完成）
    assert links == ['https://panthera.org/cat/small-cats'] * 3


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


def test_extract_content_blocks_skips_figcaption_duplicate_video_and_text():
    html = '''
    <html>
      <body>
        <main>
          <h1>News</h1>
          <figure class="wp-block-video">
            <video height="720" width="1280" controls src="https://home.cern/wp-content/uploads/2026/04/CDS-OPEN-VIDEO-2022-334-001.mp4"></video>
            <figcaption>
              <span>(Video: CERN)</span>
              <span class="cds-source-attribution">Source: <a href="https://videos.cern.ch/api/files/4dc026a6-95e7-45ae-a91a-65eb25d3a69c/720p.mp4" target="_blank">CERN (CDS)</a></span>
            </figcaption>
          </figure>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    videos = [b for b in blocks if isinstance(b, dict) and b['type'] == 'video']
    texts = [b for b in blocks if isinstance(b, str)]
    assert len(videos) == 1
    assert 'Source:' not in ''.join(texts)
    assert 'Video: CERN' not in ''.join(texts)


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


def test_extract_content_blocks_keeps_cutoff_marker_block_but_discards_after_it():
    """当前实现保留标记本身，但忽略其后的正文内容；视频仍然保留。"""
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

    blocks = extract_content_blocks(html, 'https://www.unicef.org/careers/es')
    texts = [b for b in blocks if isinstance(b, str)]
    assert 'Careers' in texts
    assert 'Work with us' in texts
    assert 'Noticias y testimonios de profesionales' in texts
    assert 'This content should be ignored.' not in texts
    videos = [b for b in blocks if isinstance(b, dict) and b['type'] == 'video']
    assert len(videos) == 1
    assert videos[0]['note'] == 'UNICEF Division of Human Resources'


def test_extract_content_blocks_cutoff_matches_class_name_marker():
    """截断标记既可命中文本，也可命中 class 名称，如 related-articles。"""
    html = '''
    <html>
      <body>
        <main>
          <h1>Title</h1>
          <p>Keep me.</p>
          <div class="related-articles">
            <h2>Related Articles</h2>
            <p>Should be cut.</p>
          </div>
          <p>After marker content should be removed.</p>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com', cutoff_markers=('Related Articles',))
    texts = [b for b in blocks if isinstance(b, str)]
    assert 'Title' in texts
    assert 'Should be cut.' not in texts
    assert 'After marker content should be removed.' not in texts


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
    """导航噪音中的标记不应触发截断；正文内的标题仍会保留。"""
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
    assert 'Title' in texts
    assert 'This is the news section, should be cut.' in texts
    videos = [b for b in blocks if isinstance(b, dict) and b['type'] == 'video']
    assert len(videos) == 0


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


def test_extract_content_blocks_only_excludes_exact_noise_class_matches():
    """仅当 class 集合完全命中 TEXT_NOISE_CLASSES 时才跳过；混合 class 不应被误判。"""
    html = '''
    <html>
      <body>
        <main>
          <div class="field__item">
            <p>Noise text should be ignored.</p>
          </div>
          <div class="field__item extra">
            <p>Real content should stay.</p>
          </div>
          <div class="wp-block-cover alignfull">
            <p>Cover block text should be ignored.</p>
          </div>
          <div class="wp-block-cover alignfull other-class">
            <p>Real cover content should stay.</p>
          </div>
        </main>
      </body>
    </html>
    '''

    blocks = extract_content_blocks(html, 'https://example.com')
    texts = [b for b in blocks if isinstance(b, str)]
    assert 'Noise text should be ignored.' not in texts
    assert 'Cover block text should be ignored.' not in texts
    assert 'Real content should stay.' in texts
    assert 'Real cover content should stay.' in texts


def test_extract_content_blocks_keeps_404_link_page_content():
    """在提取阶段，404/错误页链接不再直接掩盖页面内容；只在分析阶段做排除。"""
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
    assert 'Page not found' in blocks
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert len(media_items) == 2


def test_extract_content_blocks_keeps_error_404_link_page_content():
    """error-404 链接在提取阶段也不会直接清空页面内容。"""
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
    media_items = [b for b in blocks if isinstance(b, dict)]
    assert len(media_items) == 1
    assert media_items[0]['type'] == 'video'


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
        'https://www.example.com': '''
        <html><body><main>
          <a href="/careers.html">careers</a>
          <a href="/about.css">about-style</a>
          <a href="/brochure.pdf">brochure</a>
          <a href="https://other.com/page">external</a>
          <video src="/v0.mp4"></video>
        </main></body></html>
      ''',
        'https://www.example.com/careers.html': '''
        <html><body><main>
          <iframe src="https://www.youtube.com/embed/abc"></iframe>
        </main></body></html>
      ''',
    }

    def fake_fetch(url, **kwargs):
        return {'html': pages[url], 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    result = scrape_site('https://www.example.com', tmp_path, max_depth=1, max_pages=10)

    assert result['page_count'] == 2
    assert result['video_count'] == 2
    assert result['page_count'] == 2
    assert result['video_count'] == 2
    assert result['image_count'] == 0
    assert result['failed_count'] == 0


def test_scrape_site_follows_header_nav_links(tmp_path, monkeypatch):
    pages = {
        'https://www.example.com': '''
        <html><body>
          <header>
            <nav><a href="/about">About</a></nav>
          </header>
          <main><p>Home</p></main>
        </body></html>
        ''',
        'https://www.example.com/about': '<html><body><main><p>About us</p></main></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    result = scrape_site('https://www.example.com', tmp_path, max_depth=1, max_pages=10)
    assert result['page_count'] == 2
    assert result['failed_count'] == 0


def test_scrape_site_unlimited_depth_and_pages(tmp_path, monkeypatch):
    pages = {
    'https://www.example.com': '<html><body><a href="/a.html">a</a></body></html>',
    'https://www.example.com/a.html': '<html><body><a href="/b.html">b</a></body></html>',
    'https://www.example.com/b.html': '<html><body><p>end</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    result = scrape_site('https://www.example.com', tmp_path, max_depth=-1, max_pages=0)
    assert result['page_count'] == 3


def test_scrape_site_skip_parse_error_and_continue(tmp_path, monkeypatch):
    pages = {
  'https://www.example.com': '<html><body><a href="/bad.html">bad</a><a href="/good.html">good</a></body></html>',
  'https://www.example.com/bad.html': '<html><body><main>bad page<video src="/bad.mp4"></video></main></body></html>',
  'https://www.example.com/good.html': '<html><body><main><p>ok</p><video src="/ok.mp4"></video></main></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    original_save = scraper_module._save_html_snapshot

    def flaky_save(url, html, outdir, page_index, timestamp, **kwargs):
        if url.endswith('/bad.html'):
            raise ValueError('parse error')
        return original_save(url, html, outdir, page_index, timestamp, **kwargs)

    monkeypatch.setattr('src.scraper._save_html_snapshot', flaky_save)

    result = scrape_site('https://www.example.com', tmp_path, max_depth=1, max_pages=10)
    assert result['page_count'] == 2  # /（无视频）和 good.html（有视频且成功）
    assert result['failed_count'] == 1  # bad.html 有视频但解析失败
    assert 'https://www.example.com/bad.html' in result['failed']


def test_scrape_site_persists_failed_fetch_pages(tmp_path, monkeypatch):
    pages = {
      'https://www.example.com': '<html><body><a href="/bad.html">bad</a><a href="/good.html">good</a></body></html>',
      'https://www.example.com/good.html': '<html><body><main><p>ok</p></main></body></html>',
    }

    def fake_fetch(url, **kwargs):
      if url.endswith('/bad.html'):
        raise RuntimeError('network down')
      return {'html': pages[url], 'content_type': 'text/html'}

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)

    # save_site_html returns the save-step result which includes fetch failures
    result = scraper_module.scrape_site('https://www.example.com', tmp_path, max_depth=1, max_pages=10)

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
    assert failed_url == 'https://www.example.com/bad.html'
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


def test_challenge_detector_triggered_by_503_page():
    # Regression: the '503 Service Temporarily Unavailable' marker must match
    # case-insensitively against the lowercased sample (was broken with
    # mixed-case marker, e.g. nginx 503 pages were not flagged).
    html = '''<html><head><title>503 Service Temporarily Unavailable</title></head>
    <body><center><h1>503 Service Temporarily Unavailable</h1></center>
    <hr><center>nginx/1.28.2</center></body></html>
    '''
    assert scraper_module._is_challenge_or_block_page(html) is True


def test_challenge_detector_requires_full_phrase_match():
    html = '''<html><body>
        <p>The resource you are looking for has been removed, had its name changed, or is temporarily unavailable.</p>
    </body></html>'''
    assert scraper_module._is_challenge_or_block_page(html) is False


def test_challenge_detector_returns_matching_marker():
    html = '<html><title>Just a moment...</title><body>Checking your browser</body></html>'
    soup = scraper_module.BeautifulSoup(html, 'html.parser')
    assert scraper_module._find_challenge_marker(soup) == 'Just a moment'
    assert scraper_module._is_challenge_or_block_page(html) is True


def test_challenge_detector_is_case_sensitive_and_full_phrase_only():
    html = '<html><body>just a moment and checking your browser</body></html>'
    soup = scraper_module.BeautifulSoup(html, 'html.parser')
    assert scraper_module._find_challenge_marker(soup) is None
    assert scraper_module._is_challenge_or_block_page(html) is False


def test_fetch_html_playwright_mode(monkeypatch):
    monkeypatch.setattr('src.scraper.fetch_html_with_playwright', lambda *args, **kwargs: {'html': '<html>pw</html>', 'content_type': 'text/html', 'final_url': 'https://example.com/final'})
    result = fetch_html('https://example.com', renderer='playwright')
    assert 'pw' in result['html']
    assert result['final_url'] == 'https://example.com/final'


def test_fetch_html_returns_final_url_after_redirect(monkeypatch):
    class DummyResp:
        url = 'https://example.com/final'
        headers = {'content-type': 'text/html; charset=utf-8'}
        encoding = 'utf-8'
        apparent_encoding = 'utf-8'
        text = '<html><body>redirected</body></html>'

        def raise_for_status(self):
            return None

    monkeypatch.setattr('src.scraper.requests.get', lambda *args, **kwargs: DummyResp())
    result = fetch_html('https://example.com/start', renderer='requests')
    assert result['final_url'] == 'https://example.com/final'
    assert result['html'] == '<html><body>redirected</body></html>'


def test_fetch_html_playwright_aborted_pdf_returns_empty_html(monkeypatch):
    class FakeRequest:
        def __init__(self):
            self.headers = {'content-type': 'application/pdf'}
        def get(self, url):
            class FakeResp:
                headers = {'content-type': 'application/pdf'}
                def body(self):
                    return b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'
            return FakeResp()

    class FakePage:
        request = FakeRequest()

        def route(self, *args, **kwargs):
            return None

        def goto(self, *args, **kwargs):
            raise Exception('Page.goto: net::ERR_ABORTED at https://example.com/file.pdf')

        def close(self):
            return None

    class FakeContext:
        def new_page(self):
            return FakePage()

        def close(self):
            return None

    monkeypatch.setattr('src.scraper._get_thread_browser', lambda *args, **kwargs: object())
    monkeypatch.setattr('src.scraper._get_thread_context', lambda *args, **kwargs: FakeContext())
    monkeypatch.setattr('src.scraper.sync_playwright', lambda: object())

    result = scraper_module.fetch_html_with_playwright('https://example.com/file.pdf', wait_seconds=10, headless=True)

    assert result['content_type'] == 'application/pdf'
    assert result['html'] == ''


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
        'https://www.example.com': '<html><body><a href="/a.html">a</a></body></html>',
        'https://www.example.com/a.html': '<html><body><a href="/b.html">b</a></body></html>',
        'https://www.example.com/b.html': '<html><body><p>end</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'text/html'})

    progress = []

    def callback(done, total, url):
        progress.append((done, total, url))

    result = scrape_site(
        'https://www.example.com',
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
        'https://www.example.com': '<html><body><a href="/img/noext">img</a><a href="/ok.html">ok</a></body></html>',
        'https://www.example.com/img/noext': 'JFIF_BINARY_BYTES',
        'https://www.example.com/ok.html': '<!doctype html><html><body><p>ok</p></body></html>',
    }

    monkeypatch.setattr('src.scraper.fetch_html', lambda url, **kwargs: {'html': pages[url], 'content_type': 'image/jpeg' if 'noext' in url else 'text/html'})

    # Use save_site_html directly to see save-step results (including failures)
    result = scraper_module.save_site_html('https://www.example.com', tmp_path, max_depth=1, max_pages=10)
    # All 3 pages saved (non-HTML content no longer rejected)
    assert result['saved_count'] == 3
    assert result['failed_count'] == 0
    # Verify content_type was persisted to SQLite
    import sqlite3
    from src.scraper import _scrape_db_path
    db = _scrape_db_path('https://example.com', tmp_path)
    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT url, content_type FROM pages"))
    conn.close()
    assert rows.get('https://www.example.com/img/noext') == 'image/jpeg'
    assert rows.get('https://www.example.com/ok.html') == 'text/html'


def test_save_site_html_persists_title_in_pages_table(monkeypatch, tmp_path):
    import sqlite3

    def fake_fetch(url, **kwargs):
        return {
            'html': '<html><head><title>Example Title</title></head><body>ok</body></html>',
            'content_type': 'text/html',
      'final_url': 'https://www.example.com/final',
        }

    monkeypatch.setattr('src.scraper.fetch_html', fake_fetch)
    scraper_module.save_site_html('https://www.example.com', tmp_path, max_depth=0, max_pages=10)

    db_path = scraper_module._scrape_db_path('https://www.example.com', tmp_path)
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
      "SELECT title, final_url FROM pages WHERE url = ?",
        ('https://www.example.com/final',),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 'Example Title'
    assert row[1] == 'https://www.example.com/final'


def test_load_html_cache_from_db_preserves_final_url(tmp_path):
    import sqlite3

    html_path = tmp_path / 'page.html'
    html_path.write_text('<html><head><title>Recovered Title</title></head><body>x</body></html>', encoding='utf-8')

    db_path = scraper_module._scrape_db_path('https://example.com', tmp_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pages ("
        "  url TEXT PRIMARY KEY,"
      "  final_url TEXT DEFAULT NULL,"
        "  html_path TEXT DEFAULT NULL,"
        "  content_type TEXT NOT NULL DEFAULT '',"
        "  title TEXT DEFAULT NULL,"
        "  links TEXT DEFAULT NULL,"
        "  video_count INTEGER NOT NULL DEFAULT -1,"
        "  image_count INTEGER NOT NULL DEFAULT -1,"
        "  created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))"
        ")"
    )
    conn.execute(
      "INSERT OR REPLACE INTO pages (url, final_url, html_path, content_type, title, video_count, image_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
      ('https://example.com', 'https://example.com/final', str(html_path), 'text/html', '', -1, -1),
    )
    conn.commit()
    conn.close()

    cache = scraper_module._load_html_cache_from_db('https://example.com', tmp_path)

    assert cache[0]['title'] == ''
    assert cache[0]['final_url'] == 'https://example.com/final'


def test_save_site_html_reuses_cached_local_html(monkeypatch, tmp_path):
    pages = {
        'https://www.example.com': '<html><body><a href="/a.html">a</a></body></html>',
        'https://www.example.com/a.html': '<html><body><p>a</p></body></html>',
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

    first = scraper_module.save_site_html('https://www.example.com', tmp_path, max_depth=1, max_pages=10)
    assert first['saved_count'] == 2
    assert fetch_calls['count'] == 2
    assert set(_db_urls()) == {'https://www.example.com', 'https://www.example.com/a.html'}

    def fail_fetch(url, **kwargs):
        raise AssertionError(f'fetch_html should not be called for cached URL: {url}')

    monkeypatch.setattr('src.scraper.fetch_html', fail_fetch)

    second = scraper_module.save_site_html('https://www.example.com', tmp_path, max_depth=1, max_pages=10)
    assert second['saved_count'] == 2
    assert second['failed_count'] == 0
    assert set(_db_urls()) == {'https://www.example.com', 'https://www.example.com/a.html'}


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

    db_path = scraper_module._scrape_db_path(start_url, tmp_path)
    conn = scraper_module._init_db(db_path)
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


def test_analyze_skips_duplicate_final_url(tmp_path):
    import sqlite3

    start_url = 'https://example.com'
    html_a = tmp_path / 'page_a.html'
    html_b = tmp_path / 'page_b.html'
    html = '<html><body><main><video src="/intro.mp4"></video></main></body></html>'
    html_a.write_text(html, encoding='utf-8')
    html_b.write_text(html, encoding='utf-8')

    db_path = scraper_module._scrape_db_path(start_url, tmp_path)
    conn = scraper_module._init_db(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO pages (url, final_url, html_path, content_type, video_count, image_count)"
        " VALUES (?, ?, ?, ?, -1, -1)",
        [
            ('https://example.com/a', 'https://example.com/final', str(html_a), 'text/html'),
            ('https://example.com/b', 'https://example.com/final', str(html_b), 'text/html'),
        ],
    )
    conn.commit()
    conn.close()

    result = scraper_module.analyze_saved_html(start_url, tmp_path)

    assert result['page_count'] == 1
    conn = sqlite3.connect(str(db_path))
    rows = dict(conn.execute("SELECT url, video_count FROM pages ORDER BY url"))
    conn.close()
    assert rows['https://example.com/a'] == 1
    assert rows['https://example.com/b'] == -1


def test_analyze_output_filename_does_not_duplicate_page_name(tmp_path):
    start_url = 'https://example.com'
    analyze_dir = tmp_path / 'analyze'
    analyze_dir.mkdir()
    src_path = tmp_path / '0913_threat-human-cat-conflict_20260809132126.html'
    src_path.write_text('<html></html>', encoding='utf-8')

    dst_html, json_path = scraper_module._save_analyze_output(
        'https://www.panthera.org/threat-human-cat-conflict',
        src_path,
        analyze_dir,
        page_index=64,
        timestamp=scraper_module._extract_timestamp_from_stem(src_path),
        content_blocks=[],
    )

    assert Path(dst_html).name == '0064_threat-human-cat-conflict_20260809132126.html'
    assert Path(json_path).name == '0064_threat-human-cat-conflict_20260809132126.json'


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
