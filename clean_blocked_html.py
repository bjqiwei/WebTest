"""
遍历目录下的 HTML 文件，若包含 _is_challenge_or_block_page 特征则删除。
用法:
    python clean_blocked_html.py <目录路径>
    python clean_blocked_html.py <目录路径> --dry-run   # 仅列出，不删除
"""

import argparse
import sys
from pathlib import Path

# 确保能找到 src.scraper
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.scraper import _is_challenge_or_block_page


def clean_html_dir(root_dir: Path, dry_run: bool = False):
    if not root_dir.is_dir():
        print(f'错误: {root_dir} 不是有效目录')
        return

    html_files = list(root_dir.rglob('*.html'))
    total = len(html_files)
    deleted = 0

    for idx, fp in enumerate(html_files, start=1):
        try:
            html = fp.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            print(f'[{idx}/{total}] 读取失败: {fp} ({e})')
            continue

        if _is_challenge_or_block_page(html):
            print(f'[{idx}/{total}] 命中特征: {fp}')
            if not dry_run:
                try:
                    fp.unlink()
                    deleted += 1
                    print(f'  -> 已删除')
                except Exception as e:
                    print(f'  -> 删除失败: {e}')
            else:
                deleted += 1
        #else:
            # 非阻塞页面，仅显示进度
            #if total <= 200 or idx % 50 == 0:
                 #print(f'[{idx}/{total}] 跳过: {fp.name}')

    print(f'\n完成。共扫描 {total} 个 HTML 文件，命中并{"（模拟）" if dry_run else ""}删除 {deleted} 个。')


def main():
    parser = argparse.ArgumentParser(description='删除包含挑战/封锁页面特征的 HTML 文件')
    parser.add_argument('directory', type=str, help='要扫描的目录路径')
    parser.add_argument('--dry-run', action='store_true', help='仅列出匹配文件，不执行删除')
    args = parser.parse_args()

    clean_html_dir(Path(args.directory), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
