"""
遍历目录下的 HTML 文件，若包含 _is_challenge_or_block_page 特征则删除（多线程 10 路）。
用法:
    python clean_blocked_html.py <目录路径>
    python clean_blocked_html.py <目录路径> --dry-run   # 仅列出，不删除
"""

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 确保能找到 src.scraper
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bs4 import BeautifulSoup
from src.scraper import _find_challenge_marker

NUM_THREADS = 10

_lock = threading.Lock()


def _chunks(lst, n):
    """将 lst 尽量均匀地分成 n 份，返回每份的 (起始索引, 长度)。"""
    size = len(lst)
    chunks = []
    start = 0
    for i in range(n):
        # 前 (size % n) 份多分一个元素
        chunk_len = size // n + (1 if i < size % n else 0)
        chunks.append(lst[start:start + chunk_len])
        start += chunk_len
    return chunks


def _process_files(file_batch, thread_id, dry_run):
    """处理一批文件，返回该批次的删除数。"""
    local_deleted = 0
    # 该批次起始编号由主线程提前算出
    for fp in file_batch:
        try:
            html = fp.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            with _lock:
                print(f'[线程{thread_id}] 读取失败: {fp} ({e})')
            continue

        marker = _find_challenge_marker(BeautifulSoup(html, 'html.parser'))
        if marker:
            with _lock:
                print(f'[线程{thread_id}] 命中特征: {marker}, 文件: {fp}')
            if not dry_run:
                try:
                    fp.unlink()
                    local_deleted += 1
                except Exception as e:
                    with _lock:
                        print(f'[线程{thread_id}] 删除失败: {fp} ({e})')
            else:
                local_deleted += 1
    return local_deleted


def clean_html_dir(root_dir: Path, dry_run: bool = False):
    if not root_dir.is_dir():
        print(f'错误: {root_dir} 不是有效目录')
        return

    html_files = list(root_dir.rglob('*.html'))
    total = len(html_files)
    print(f'共发现 {total} 个 HTML 文件，使用 {NUM_THREADS} 个线程处理。')

    batches = _chunks(html_files, NUM_THREADS)
    # 过滤掉空批次
    non_empty = [(i, b) for i, b in enumerate(batches, start=1) if b]

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        fut_to_thread = {
            executor.submit(_process_files, batch, tid, dry_run): tid
            for tid, batch in non_empty
        }
        deleted = 0
        for fut in as_completed(fut_to_thread):
            tid = fut_to_thread[fut]
            try:
                deleted += fut.result()
            except Exception as e:
                print(f'[线程{tid}] 发生异常: {e}')

    print(f'\n完成。共扫描 {total} 个 HTML 文件，命中并{"（模拟）" if dry_run else ""}删除 {deleted} 个。')


def main():
    parser = argparse.ArgumentParser(description='删除包含挑战/封锁页面特征的 HTML 文件（10 线程）')
    parser.add_argument('directory', type=str, help='要扫描的目录路径')
    parser.add_argument('--dry-run', action='store_true', help='仅列出匹配文件，不执行删除')
    args = parser.parse_args()

    clean_html_dir(Path(args.directory), dry_run=args.dry_run)


if __name__ == '__main__':
    main()
