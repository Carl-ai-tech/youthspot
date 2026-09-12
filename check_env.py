"""確認 .env 設好了沒。**刻意不印出金鑰內容** —— 只回報長度與前綴。

存在的理由：金鑰貼錯（多空白、少字元、貼到註解行）是最常見的卡關原因，
但為了查這個而 cat .env 會把金鑰印在終端機上，之後截圖、投影、
貼給別人看的時候就跟著出去了。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm.backend import _load_dotenv  # noqa: E402

_load_dotenv()

key = os.environ.get("ANTHROPIC_API_KEY")
backend = os.environ.get("YOUTHLENS_LLM_BACKEND", "stub")
model = os.environ.get("YOUTHLENS_ANTHROPIC_MODEL", "(未設定)")

print()
if not key:
    print("❌ 沒有讀到 ANTHROPIC_API_KEY")
    print("   檢查 .env 裡是不是這樣（等號後面不要有空格、不要引號）：")
    print("       ANTHROPIC_API_KEY=sk-ant-...")
else:
    print(f"✅ 讀到金鑰　長度 {len(key)}　開頭 {key[:7]}…")
    if key.strip() != key:
        print("   ⚠️ 前後有空白，貼的時候多帶到了 —— 會認證失敗，回去刪掉")
    if not key.startswith("sk-ant-"):
        print("   ⚠️ 開頭不是 sk-ant-，可能貼到別的東西了")

print(f"   後端　{backend}")
print(f"   模型　{model}")

try:
    import anthropic  # noqa: F401
    print("   anthropic 套件　已安裝")
except ImportError:
    print("   ❌ anthropic 套件未安裝 → pip install anthropic")
print()
