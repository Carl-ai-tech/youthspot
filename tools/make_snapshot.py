"""把儀表板「畫完之後」的 DOM 存成靜態 HTML。—— 給只會抓原始 HTML 的評審工具看

preview.html 的內容幾乎全是瀏覽器用 JS 從內嵌的 unified.json 畫出來的：
原始檔裡只有骨架加一坨 JSON，用 curl 或文字抓取器讀到的是「舊版」或「空的」——
不是沒更新，是沒有渲染。

這支用 headless Chrome 開 preview.html?report=layout（所有區塊展開、封面填好），
把渲染後的 DOM 倒出來，拿掉 <script>（那 480 KB 的資料與程式碼對讀者沒意義），
存成 snapshot.html。serve.py 的白名單有放行 /snapshot.html。

    python tools/make_snapshot.py                     # 從 localhost:8787 抓
    python tools/make_snapshot.py http://localhost:9000/preview.html
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_scan_fixture import find_chrome  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "snapshot.html"


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787/preview.html"
    url = src + ("&" if "?" in src else "?") + "report=layout"
    chrome = find_chrome()
    if chrome is None:
        print("找不到 Chrome")
        return 1
    r = subprocess.run(
        [str(chrome), "--headless=new", "--disable-gpu", "--virtual-time-budget=8000", "--dump-dom", url],
        capture_output=True, timeout=120,
    )
    html = r.stdout.decode("utf-8", "replace")
    if "青年統計簡報" not in html:
        print("渲染後的 DOM 裡沒有封面，可能 serve.py 沒開或頁面出錯")
        return 1
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    banner = ('<div style="background:#fff3cd;color:#5a4300;padding:.6rem 1rem;font:14px system-ui">'
              f'這是 YouthLens 儀表板在 {stamp} 渲染後的靜態快照（所有區塊已展開，互動功能不可用）。'
              '互動版在 /preview.html。</div>')
    html = html.replace("<body>", "<body>" + banner, 1)
    OUT.write_text(html, encoding="utf-8")
    print(f"寫出 {OUT}　{OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
