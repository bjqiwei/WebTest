from pathlib import Path
from src.scraper import extract_videos


def test_extract_videos_from_html():
    html = '''
    <html>
      <body>
        <video src="/media/v1.mp4"></video>
        <figure><video><source src="https://cdn.example.com/v2.webm"></source></video><figcaption>Vid 2</figcaption></figure>
        <a href="https://cdn.example.com/movie.mp4">download</a>
      </body>
    </html>
    '''

    videos = extract_videos(html, 'https://example.com')
    assert any('v1.mp4' in v['url'] for v in videos)
    assert any('v2.webm' in v['url'] for v in videos)
    assert any('movie.mp4' in v['url'] for v in videos)
