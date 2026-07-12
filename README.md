# HTTrack (Python)

简单的网页抓取器：将页面保存为本地 HTML，并提取正文中的媒体（视频、图片）到 JSON 文件，支持同域名多页面递归抓取。

快速开始:

1. 创建虚拟环境并安装依赖：

```bash
python -m venv venv
venv\Scripts\activate    # Windows
pip install -r requirements.txt
python -m playwright install chromium
```

2. 单页面抓取示例：

```bash
python scrape.py https://example.com -o output
```

3. 多页面递归抓取示例：

```bash
python scrape.py https://www.bosch.com/careers -o output -d 1 -m 20
```

4. 递归抓取同域名内所有可达页面：

```bash
python scrape.py https://www.bosch.com/careers -o output --all
```

参数说明：

- `-d, --depth`：递归深度（默认 0，仅当前页面）
- `-m, --max-pages`：最多抓取页面数量（默认 20）
- `--all`：抓取同域名内所有可达页面（等价于 `-d -1 -m 0`）
- `--renderer`：抓取模式，`auto`（默认，requests失败/挑战页回退playwright）、`requests`、`playwright`

输出说明：

- 每个页面都会生成：`0001_path_html_时间.html` 和 `0001_path_html_时间.json`
- 同时生成一个汇总文件：`*_summary.json`
- 终端输出格式：`X个页面，Y个媒体元素`

页面 JSON 结构：

- `original_link`: 当前页面 URL
- `content_blocks`: 按页面顺序组织的正文与媒体块
- `extra`: 预留扩展字段，默认 `{}`

说明: 项目基于静态抓取 (requests + BeautifulSoup)。若需要处理大量 JS 渲染的站点，可后续加入 Playwright/Selenium 支持。
