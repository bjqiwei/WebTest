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
python scrape.py scrape https://example.com -o output
```

也可以拆成两步：

```bash
python scrape.py save https://example.com -o output
python scrape.py analyze output/<site>_cache.json
```

3. 多页面递归抓取示例：

```bash
python scrape.py scrape https://www.bosch.com/careers -o output -d 1 -m 20
```

4. 递归抓取同域名内所有可达页面：

```bash
python scrape.py scrape https://www.bosch.com/careers -o output --all
```

参数说明：

- `-d, --depth`：递归深度（默认 0，仅当前页面）
- `-m, --max-pages`：最多抓取页面数量（默认 20）
- `--all`：抓取同域名内所有可达页面（等价于 `-d -1 -m 0`）
- `--renderer`：抓取模式，`auto`（默认，requests失败/挑战页回退playwright）、`requests`、`playwright`
- `--headed`：使用 Playwright 可视化浏览器（便于人工完成挑战）
- `--wait-seconds`：Playwright 页面加载后的额外等待秒数（默认 5）

输出说明：

- 每个页面都会生成：`0001_path_html_时间.html` 和 `0001_path_html_时间.json`
- `save` 会生成抓取缓存文件：`*_cache.json`
- `analyze` 会基于 `*_cache.json` 生成汇总文件：`*_summary.json`
- 抓取失败的页面会保存到 `output/failed/`
- 终端输出格式：`X个页面，Y个媒体元素`

页面 JSON 结构：

- `original_link`: 当前页面 URL
- `content_blocks`: 按页面顺序组织的正文与媒体块
- `extra`: 预留扩展字段，默认 `{}`

说明: 项目默认使用 requests + BeautifulSoup，遇到挑战页或你指定 `--renderer playwright` 时会切换到 Playwright 渲染抓取。

挑战页说明（Cloudflare）:

- 如果页面显示“正在进行安全验证”，建议使用 `--renderer playwright --headed --wait-seconds 30`
- `--headed` 会打开可视化浏览器，便于人工完成验证
- 验证通过后脚本会继续抓取并保存 HTML（会等待页面 body 就绪后再保存）
