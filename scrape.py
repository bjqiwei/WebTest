import argparse
from pathlib import Path
from src.scraper import scrape_site


def main():
    parser = argparse.ArgumentParser(description='Save page HTML and extract video URLs to JSON')
    parser.add_argument('url', help='Target URL to scrape')
    parser.add_argument('-o', '--out', default='output', help='Output directory')
    parser.add_argument('-d', '--depth', type=int, default=0, help='Recursive depth for same-domain pages (<0 means unlimited)')
    parser.add_argument('-m', '--max-pages', type=int, default=20, help='Maximum number of pages to crawl (<=0 means unlimited)')
    parser.add_argument('--all', action='store_true', help='Crawl all reachable pages in the same domain')
    parser.add_argument('--renderer', choices=['auto', 'requests', 'playwright'], default='auto', help='Page rendering mode')
    parser.add_argument('--headed', action='store_true', help='Run Playwright with visible browser window')
    parser.add_argument('--wait-seconds', type=float, default=5.0, help='Extra wait time after page load in Playwright mode')
    args = parser.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    max_depth = args.depth
    max_pages = args.max_pages
    if args.all:
        max_depth = -1
        max_pages = 0

    result = scrape_site(
        args.url,
        outdir,
        max_depth=max_depth,
        max_pages=max_pages,
        renderer=args.renderer,
        playwright_headless=not args.headed,
        playwright_wait_seconds=max(args.wait_seconds, 0.0),
    )
    print('抓取完成')
    print(f'{result["page_count"]}个页面，{result.get("media_count", 0)}个媒体元素')
    print(f'视频数: {result["video_count"]}')
    print(f'失败数: {result.get("failed_count", 0)}')
    if result['pages']:
        print(f'首页HTML: {result["pages"][0]["html_path"]}')
        print(f'首页JSON: {result["pages"][0]["json_path"]}')
    if result.get('failed_reasons'):
        print('失败示例:')
        for u, reason in list(result['failed_reasons'].items())[:3]:
            print(f'- {u} -> {reason}')
    print(f'汇总JSON: {result["summary_path"]}')


if __name__ == '__main__':
    main()
