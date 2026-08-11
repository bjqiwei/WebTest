# HTTrack (Python)

简单的网页抓取器：将页面保存为本地 HTML，并提取正文中的媒体（视频、图片）到 JSON 文件，支持同域名多页面递归抓取。使用 SQLite 管理抓取缓存和分析状态。

## 快速开始

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 命令

### `scrape` — 抓取 + 分析（一步完成）

```bash
python scrape.py scrape https://example.com -o output
```

### `save` — 仅抓取保存 HTML

```bash
python scrape.py save https://example.com -o output
```

### `analyze` — 分析已保存的 HTML

```bash
python scrape.py analyze https://example.com -o output
```

分析完成后，如需删除不含视频页面的本地 HTML（保留 SQLite 记录）：

```bash
python scrape.py analyze https://example.com -o output --delete-html-no-video
```

## 示例

**单页面抓取：**

```bash
python scrape.py scrape https://example.com -o output
```

**分两步执行：**

```bash
python scrape.py save https://example.com -o output
python scrape.py analyze https://example.com -o output
```

**递归抓取（深度 1，最多 20 页）：**

```bash
python scrape.py scrape https://www.bosch.com/careers -o output -d 1 -m 20
```

**抓取同域名内所有可达页面：**

```bash
python scrape.py scrape https://www.bosch.com/careers -o output --all
```

**使用 Playwright 渲染 + CDP 连接现有 Chrome（适合复杂页面）：**

```bash
python scrape.py save https://www.unicef.org -o d:\web\unicef --all --renderer playwright --wait-seconds 90 --cdp-url http://127.0.0.1:9222 --concurrency 1
```
python scrape.py save https://www.unfpa.org -o d:\web\unfpa --all --renderer playwright --wait-seconds 30 --cdp-url http://127.0.0.1:9222 --concurrency 1

python scrape.py save https://www.nature.org -o d:\web\nature --all --renderer playwright --wait-seconds 30 --cdp-url http://127.0.0.1:9222 --concurrency 1

python scrape.py save https://www.panthera.org  -o d:\web\panthera --all --renderer playwright --wait-seconds 30 --cdp-url http://127.0.0.1:9222 --concurrency 1

python scrape.py save https://www.home.cern  -o d:\web\home --all --renderer playwright --wait-seconds 30 --cdp-url http://127.0.0.1:9222 --concurrency 1

python scrape.py save https://www.ifad.org -o d:\web\ifad --all --renderer playwright --wait-seconds 30 --cdp-url http://127.0.0.1:9222 --concurrency 1
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `-o, --out` | 输出目录（默认 `output`） |
| `-d, --depth` | 递归深度，默认 0（仅当前页面）；负数表示无限制 |
| `-m, --max-pages` | 最多抓取页面数，默认 20；<=0 表示无限制 |
| `--all` | 抓取同域名内所有可达页面（等价于 `-d -1 -m 0`） |
| `--renderer` | 渲染模式：`auto`（默认，requests 失败或检测到挑战页时回退 Playwright）、`requests`、`playwright` |
| `--headed` | 使用 Playwright 可视化浏览器（便于手动完成验证） |
| `--wait-seconds` | Playwright 页面加载后的额外等待秒数（默认 5） |
| `--cdp-url` | 连接到现有 Chrome 浏览器（例如 `http://127.0.0.1:9222`） |
| `--concurrency` | 并发抓取线程数（默认 1） |
| `--delete-html-no-video` | （`analyze`/`scrape`）分析后删除不含视频页面的本地 HTML 文件，SQLite 记录保留 |

## 输出说明

### 目录结构

```
output/
├── analyze/                    # 分析结果（仅含视频的页面）
│   ├── 0001_path_html_时间.html
│   ├── 0001_path_html_时间.json
│   └── ...
├── failed/                     # 抓取失败的页面快照
│   └── ...
├── example.com_scrape.db       # SQLite 数据库（pages + failed_pages 表）
└── scrape.log                  # 运行日志
```

### JSON 结构

```json
{
  "original_link": "https://example.com/page",
  "content_blocks": [
    "视频和图片内容说明",
    { "type": "image", "original_url": "...", "index": 1, ... },
    "正文文本",
    { "type": "video", "original_url": "...", "index": 1, ... }
  ],
  "extra": {}
}
```

- `content_blocks` 包含文本（字符串）和媒体项（字典）混合排列
- 视频和图片各自拥有独立的序号（`index`）
- 只有包含视频的页面才会被写入 `analyze/` 目录

### 分析汇总字段

| 字段 | 说明 |
|------|------|
| `page_count` | 包含视频的页面数 |
| `video_count` | 视频总数 |
| `image_count` | 图片总数 |
| `failed_count` | 分析失败的页面数 |
| `failed_reasons` | 失败原因映射 `{url: reason}` |

## 技术说明

- 默认使用 `requests` + `BeautifulSoup` 抓取和解析
- 遇到 Cloudflare 等挑战页时自动回退到 Playwright 渲染
- 可通过 `--renderer playwright` 强制使用 Playwright
- 使用 SQLite（WAL 模式）持久化页面缓存、链接和媒体统计
- 增量分析：仅扫描 `video_count = -1` 或已有视频结果需刷新的页面
- 多线程并发抓取（`--concurrency`），每个线程独立 Playwright 实例

## 挑战页说明（Cloudflare）

- 如果页面显示"正在进行安全验证"，建议使用：
  ```
  --renderer playwright --headed --wait-seconds 30
  ```
- `--headed` 会打开可视化浏览器，便于人工完成验证
- CDP 模式可以复用已登录的 Chrome 会话，减少验证触发
