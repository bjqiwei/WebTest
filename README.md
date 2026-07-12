# HTTrack (Python)

简单的网页抓取器：将页面保存为本地 HTML，并提取页面中的视频 URL 到同名 JSON 文件。

快速开始:

1. 创建虚拟环境并安装依赖：

```bash
python -m venv venv
venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

2. 运行示例：

```bash
python scrape.py https://example.com -o output
```

输出将把页面保存为 `output/<hostname>_<n>.html`，视频信息保存为相同文件名的 `.json`。

说明: 项目基于静态抓取 (requests + BeautifulSoup)。若需要处理大量 JS 渲染的站点，可后续加入 Playwright/Selenium 支持。
