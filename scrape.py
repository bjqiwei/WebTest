import argparse
from pathlib import Path
from src.scraper import scrape_url


def main():
    parser = argparse.ArgumentParser(description='Save page HTML and extract video URLs to JSON')
    parser.add_argument('url', help='Target URL to scrape')
    parser.add_argument('-o', '--out', default='output', help='Output directory')
    args = parser.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    result = scrape_url(args.url, outdir)
    print(f'Saved HTML: {result["html_path"]}')
    print(f'Saved JSON: {result["json_path"]}')


if __name__ == '__main__':
    main()
