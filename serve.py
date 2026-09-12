"""本機開發伺服器：讓 preview.html 的問答框能打到真的模型。

為什麼需要這支：瀏覽器不能直接呼叫模型 API —— 金鑰會外洩。
一定要有個伺服器在中間。9/12 那個伺服器是 Lambda；賽前就是這支。

它做兩件事：
    GET  /preview.html   只給這一頁（加掃描範例圖）。其餘一律 404 —— 見 PUBLIC_FILES
    POST /api      跟 deploy/lambda_handler.py 的 _ai() **同一支函式**

第二點是重點。這支不自己實作任何 AI 邏輯，直接 import Lambda 的 _ai()
並注入本機後端（讀 .env）。本機測過的路徑就是上線的路徑，
不會出現「本機好的、Lambda 壞的」。

零第三方套件：只用標準函式庫的 http.server。

執行：
    python serve.py            # http://localhost:8787
    python serve.py 9000       # 換埠

需要 .env 裡有 ANTHROPIC_API_KEY，或已設環境變數。
"""

from __future__ import annotations

import json
import posixpath
import sys
import traceback
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from deploy.lambda_handler import _ai  # noqa: E402
from llm.backend import load_backend  # noqa: E402

DEFAULT_PORT = 8787


# 對外只給這些。這支會透過 Cloudflare Tunnel 開到網際網路上，
# 而 SimpleHTTPRequestHandler 預設把整個資料夾當網站 —— .env 裡的金鑰、
# 原始碼、快取檔全部拿得到。白名單比黑名單安全：忘了列的東西是拿不到，不是漏出去。
PUBLIC_FILES = {"/", "/preview.html", "/snapshot.html", "/favicon.ico",
                "/preview_臺北市.html", "/preview_桃園市.html", "/preview_臺中市.html", "/preview_臺南市.html", "/preview_高雄市.html"}
PUBLIC_PREFIXES = ("/tests/fixtures/",)          # 掃描 demo 的範例圖


class Handler(SimpleHTTPRequestHandler):
    """靜態檔 + /api。"""

    def do_GET(self):
        # 先正規化再比對白名單。不然 /tests/fixtures/../../.env 的前綴看起來沒問題，
        # 底層的 translate_path 卻會把 .. 收掉、真的把 .env 端出去。
        path = posixpath.normpath(urllib.parse.unquote(self.path.split("?", 1)[0]))
        if ".." in path.split("/"):
            self.send_error(404)
            return
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/preview.html")
            self.end_headers()
            return
        if path not in PUBLIC_FILES and not path.startswith(PUBLIC_PREFIXES):
            self.send_error(404)   # 狀態列只能 latin-1，不能放中文
            return
        super().do_GET()

    def do_HEAD(self):
        self.send_error(404)

    def list_directory(self, path):  # noqa: D401
        self.send_error(404)
        return None

    # 每個請求都印一行太吵，只印 API 呼叫
    def log_message(self, fmt, *args):  # noqa: D401
        if self.path.startswith("/api"):
            super().log_message(fmt, *args)

    def end_headers(self):
        # 靜態檔的中文要靠這個，不然瀏覽器又會猜編碼
        if self.path.endswith((".html", ".json", ".js", ".css")):
            self.send_header("Cache-Control", "no-cache")
        # 雙擊開的 preview.html（file://）也要能打到這裡。只綁 127.0.0.1，
        # 所以放行所有來源不會把 API 開給外面。
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        """瀏覽器跨來源 POST 前會先問一次。"""
        self.send_response(204)
        self.end_headers()

    def guess_type(self, path):
        base = super().guess_type(path)
        if str(path).endswith((".html", ".json", ".js", ".css")) and "charset" not in base:
            return base + "; charset=utf-8"
        return base

    def do_POST(self):
        if self.path.rstrip("/") != "/api":
            self.send_error(404, "only /api accepts POST")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            action = body.get("action", "")
            result = _ai(action, body, backend=self.server.backend)
            status = 200
        except ValueError as exc:                     # 不認識的 action、壞的 JSON
            result, status = {"ok": False, "error": str(exc)}, 400
        except Exception as exc:                      # noqa: BLE001
            # 把原因印在終端機，回給前端的只有一句話
            traceback.print_exc()
            result, status = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500

        blob = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)


def main(argv: list[str]) -> int:
    port = int(argv[0]) if argv else DEFAULT_PORT
    backend = load_backend()
    if backend.name == "stub":
        print("⚠️  目前是假後端（stub）。問答框會拿到寫死的回應。")
        print("   要接真模型：在 .env 設 YOUTHLENS_LLM_BACKEND=anthropic 與金鑰。")
    else:
        print(f"後端：{backend.name}　模型：{getattr(backend, 'model', '-')}")

    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(ROOT)))
    server.backend = backend
    print(f"打開　http://localhost:{port}/preview.html")
    print("停止　Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
