"""用標準函式庫讀 ODS（政府統計表最常見的開放格式）。

ODS 就是一個 zip，裡面的 content.xml 描述表格。所以 zipfile + xml.etree 就夠了，
不需要 pandas 或 odfpy —— 這是專案維持零第三方套件的關鍵之一。

兩個容易踩的坑，都處理掉了：
  1. `number-columns-repeated` —— 連續的空白或重複值會被壓縮成一格加一個次數，
     不展開的話欄位會整排錯位。
  2. 數值存在 `office:value` 屬性裡，顯示文字才在 `<text:p>`。取數字要讀屬性，
     讀顯示文字會拿到千分位逗號跟全形空白。
"""

from __future__ import annotations

import io
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"

MAX_REPEAT = 40          # 尾端常有上千個空欄的 repeat，不設上限會炸掉記憶體


def read_ods(source: bytes | str | Path) -> list[list[str]]:
    """回傳去掉全空列的二維字串陣列。數值一律以字串回傳，呼叫端自己轉。"""
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(raw)).read("content.xml"))

    rows: list[list[str]] = []
    for tr in root.iter(TABLE + "table-row"):
        cells: list[str] = []
        for tc in tr.findall(TABLE + "table-cell"):
            repeat = min(int(tc.get(TABLE + "number-columns-repeated", 1)), MAX_REPEAT)
            value = tc.get(OFFICE + "value")
            if value is None:
                value = "".join("".join(p.itertext()) for p in tc.findall(TEXT + "p"))
            cells.extend([value.strip()] * repeat)
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            rows.append(cells)
    return rows


def download_ods(url: str, *, cache_name: str, refresh: bool = False) -> list[list[str]]:
    """抓一份 ODS 並讀成表格。網址含中文時自動處理編碼。"""
    from data.sources import CACHE_DIR, TIMEOUT, USER_AGENT

    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / cache_name
    if cached.exists() and not refresh:
        return read_ods(cached.read_bytes())

    safe = urllib.parse.quote(url, safe=":/?&=%")
    req = urllib.request.Request(safe, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read()
    cached.write_bytes(body)
    return read_ods(body)


def clean(text: str) -> str:
    """政府統計表的地區欄常有全形空白縮排與中英並列，統一清乾淨。"""
    return re.sub(r"[\s\u3000]+", "", text)


def to_float(text: str) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except (ValueError, AttributeError):
        return None
