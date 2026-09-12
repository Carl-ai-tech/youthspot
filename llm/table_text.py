"""讀 CSV／貼上的表格文字，自動辨識年齡分組。—— 命題痛點 2b「定義衝突，需自動辨識並轉換」

跟 scan_table.py 是同一條路的兩個入口：

    圖片    → 模型轉錄 → ScannedTable → 三道查核 → 引擎對齊     （scan_table.py）
    CSV／文字 → **規則解析** → ScannedTable → 三道查核 → 引擎對齊  （這支）

文字表格不需要模型就讀得出來 —— 規則比模型可靠、可重現、零成本。
模型只在**一個地方**出場：年齡標籤是非結構化文字、規則解析不了的時候
（「社會新鮮人」「應屆畢業生」「初次尋職者」），這就是 Spec §5.6 的 LLM 語意判讀。

§5.6 的設計原則照做：**模型只輸出「年齡區間 + 判讀依據」，不碰任何數值。**
判讀出來的區間標成「推定」，一路帶進 provenance。判讀有偏差也只影響區間對應，
不會污染計算 —— 計算永遠在 engine/。

自動辨識的部分（全部是規則）：
    - 分隔符：逗號、Tab、全形逗號、多個空白、「|」
    - 年齡欄：哪一欄的格子最像年齡標籤（15-19、15～24、未滿25歲、65歲以上、25至29歲…）
    - 數值欄：標題含指標關鍵字的那欄，否則第一個全是數字的欄
    - 合計列：「合計／總計／總數／Total」→ printed_total，不當成資料列
    - 地區、年份：表頭文字裡的縣市名與「113年」「2024」
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.schema import AgeBand, MetricKind  # noqa: E402

from .backend import Backend  # noqa: E402
from .scan_table import ScannedRow, ScannedTable, _strip_fence, _validate  # noqa: E402

_FULL2HALF = str.maketrans("０１２３４５６７８９～－–—〜", "0123456789-----")
_AGE_RE = re.compile(r"^(?:未滿\s*\d+|\d+\s*(?:歲)?\s*(?:以上|以下|\+)|\d+\s*[-~至到]\s*\d+)\s*(?:歲)?$")
_TOTAL_RE = re.compile(r"^(合\s*計|總\s*計|總\s*數|總\s*和|total)$", re.I)
_NUM_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?%?$")
_ROC_YEAR = re.compile(r"(?:民國)?\s*(\d{2,3})\s*年")
_AD_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
CITIES = ["新北市", "臺北市", "台北市", "桃園市", "臺中市", "台中市", "臺南市", "台南市", "高雄市",
          "基隆市", "新竹市", "嘉義市", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣",
          "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "台東縣", "澎湖縣", "金門縣", "連江縣", "全國", "臺灣地區"]
RATE_WORDS = ("率", "%", "％", "平均", "中位", "比")
METRIC_WORDS = ("人數", "人口", "就業", "失業", "勞動", "薪資", "年薪", "職缺", "數", "率")

SEMANTIC_PROMPT = """下面是統計表裡出現的年齡分組標籤，它們不是數字區間，是文字描述。
請把每一個判讀成「年齡下限、上限」，並寫出判讀依據。

嚴格規則：
1. 只做判讀，**不要碰任何數值**，不要計算。
2. 依據要是具體的（法規、學制、統計慣例），例如「青年基本法第 2 條：18–35 歲」「大學畢業 22 歲、碩士 24 歲」。
3. 判讀不了的標籤，start 與 end 都填 null，basis 說明為什麼。
4. start 與 end 都要給**具體整數**，取統計上最常用的區間；只有標籤本身寫「以上」才把 end 填 null。
   例：「社會新鮮人」→ 22–26（大學畢業後 1–4 年）、「應屆畢業生」→ 22–24、「青年」→ 18–35（青年基本法第 2 條）。

標籤：
{labels}

只輸出 JSON，不要任何其他文字：
{{"labels": {{"標籤原文": {{"start": 22, "end": 26, "basis": "判讀依據"}}}}}}"""


# ── 規則解析 ────────────────────────────────────────────────

def _norm_age(s: str) -> str:
    s = s.strip().translate(_FULL2HALF)
    s = re.sub(r"\s+", "", s)
    s = s.replace("歲及以上", "以上").replace("歲以上", "以上").replace("歲以下", "以下")
    s = re.sub(r"(\d+)至(\d+)", r"\1-\2", s)
    s = re.sub(r"(\d+)到(\d+)", r"\1-\2", s)
    s = re.sub(r"(\d+)~(\d+)", r"\1-\2", s)
    s = s.replace("歲", "")
    return s


def looks_like_age(s: str) -> bool:
    return bool(_AGE_RE.match(_norm_age(s).replace("以上", "以上").strip()))


def _to_num(s: str) -> float | None:
    t = s.strip().replace(",", "").replace("％", "%")
    if not t or not _NUM_RE.match(t):
        return None
    return float(t.rstrip("%"))


def _split(line: str) -> list[str]:
    line = line.rstrip("\r\n")
    for sep in ("\t", "，", ",", "|"):
        if sep in line:
            return [c.strip().strip('"') for c in line.split(sep)]
    return [c for c in re.split(r"\s{2,}|\s(?=\d)", line.strip()) if c != ""]


def parse_table_text(text: str) -> tuple[ScannedTable, list[str]]:
    """文字 → ScannedTable（未查核）＋ 規則解析不了的年齡標籤清單。

    回傳的表還沒跑 `_validate`，因為解析不了的標籤要先給模型判讀（§5.6），
    判讀完再一起查核。
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("沒有內容")
    rows = [_split(ln) for ln in lines]

    # 年齡欄：哪一欄「像年齡」的格子最多；合計列另外認
    ncol = max(len(r) for r in rows)
    def score_age(ci):
        return sum(1 for r in rows if ci < len(r) and (looks_like_age(r[ci]) or _TOTAL_RE.match(r[ci].strip())))
    age_col = max(range(ncol), key=score_age)
    if score_age(age_col) == 0:
        # 沒有任何一欄像年齡：找「第一欄是文字、後面有數字」的結構，把第一欄當標籤欄交給語意判讀
        age_col = 0

    # 資料列：年齡欄有文字、且某一欄有數字
    data_rows = [r for r in rows if age_col < len(r) and r[age_col].strip()
                 and any(_to_num(c) is not None for i, c in enumerate(r) if i != age_col)]
    header_rows = [r for r in rows if r not in data_rows]

    # 數值欄：標題含指標關鍵字優先；否則第一個在資料列裡全是數字的欄
    header = header_rows[-1] if header_rows else []
    def all_numeric(ci):
        return all(ci < len(r) and _to_num(r[ci]) is not None for r in data_rows)
    numeric_cols = [ci for ci in range(ncol) if ci != age_col and all_numeric(ci)]
    if not numeric_cols:
        raise ValueError("找不到數值欄：每一列的年齡標籤旁邊要有一欄數字")
    val_col = numeric_cols[0]
    for ci in numeric_cols:
        if ci < len(header) and any(w in header[ci] for w in METRIC_WORDS):
            val_col = ci
            break

    metric = header[val_col].strip() if val_col < len(header) and header[val_col].strip() else "未標示指標"
    unit = ""
    m_unit = re.search(r"[（(]([^）)]+)[）)]", metric)
    if m_unit:
        unit = m_unit.group(1)
        metric = re.sub(r"[（(][^）)]+[）)]", "", metric).strip()
    head_text = " ".join(" ".join(r) for r in header_rows)
    # 欄位標題只寫「人數」時，指標名稱通常在標題列（「求職登記人數（人）」）
    if metric in ("人數", "數值", "值", "數", "未標示指標"):
        title_text = " ".join(" ".join(r) for r in header_rows[:-1]) if len(header_rows) > 1 else ""
        m_t = re.search(r"([一-鿿]{2,12}(?:人數|人口|率|薪資|年薪|數))", title_text)
        if m_t:
            metric = m_t.group(1)
    if not unit:
        m2 = re.search(r"單位[:：]?\s*([^\s,，]+)", head_text)
        m3 = re.search(r"[（(]\s*(千人|萬人|人|%|％|萬元/年|萬元|元)\s*[）)]", head_text)
        unit = m2.group(1) if m2 else (m3.group(1) if m3 else ("%" if "率" in metric else "人"))
    kind = MetricKind.INTENSIVE if (unit in ("%", "％") or any(w in metric for w in RATE_WORDS)) else MetricKind.EXTENSIVE

    region = next((c.replace("台", "臺") for c in CITIES if c in head_text), None)
    year = None
    m_ad = _AD_YEAR.search(head_text)
    m_roc = _ROC_YEAR.search(head_text)
    if m_ad:
        year = int(m_ad.group(1))
    elif m_roc:
        year = int(m_roc.group(1)) + 1911

    out_rows: list[ScannedRow] = []
    printed_total = None
    unparsed: list[str] = []
    for r in data_rows:
        label = r[age_col].strip()
        value = _to_num(r[val_col])
        if value is None:
            continue
        if _TOTAL_RE.match(label):
            printed_total = value
            continue
        norm = _norm_age(label)
        try:
            AgeBand.parse(norm)
            out_rows.append(ScannedRow(age_label=norm, value=value))
        except ValueError:
            out_rows.append(ScannedRow(age_label=label, value=value))
            unparsed.append(label)
    if not out_rows:
        raise ValueError("沒有讀到任何資料列")

    table = ScannedTable(metric=metric, unit=unit, kind=kind, rows=out_rows, region=region, year=year,
                         printed_total=printed_total, notes="由 CSV／文字表格以規則解析，未經模型轉錄", raw=text)
    return table, unparsed


# ── §5.6 LLM 語意判讀：只在規則解析不了時 ────────────────────

def interpret_labels(labels: list[str], backend: Backend) -> dict[str, dict]:
    """{標籤: {"start", "end", "basis"}}。模型只給區間與依據，不碰數值。"""
    if not labels:
        return {}
    prompt = SEMANTIC_PROMPT.format(labels="\n".join(f"- {x}" for x in labels))
    try:
        payload = json.loads(_strip_fence(backend.complete(prompt)))
    except (json.JSONDecodeError, RuntimeError) as exc:
        return {x: {"start": None, "end": None, "basis": f"模型未回覆可解析的判讀（{exc}）"} for x in labels}
    got = payload.get("labels", {}) if isinstance(payload, dict) else {}
    out = {}
    for x in labels:
        item = got.get(x) or {}
        start, end = item.get("start"), item.get("end")
        out[x] = {"start": int(start) if isinstance(start, (int, float)) else None,
                  "end": int(end) if isinstance(end, (int, float)) else None,
                  "basis": str(item.get("basis") or "未提供依據")}
    return out


def read_table_text(text: str, backend: Backend | None = None) -> tuple[ScannedTable, dict[str, dict]]:
    """CSV／文字 → 已查核的表 ＋ 模型判讀過的標籤（含依據）。

    流程：規則解析 → 解析不了的標籤交模型判讀（標「推定」）→ 三道查核。
    沒給 backend 時解析不了的列就留在 issues 裡，不會靜靜消失。
    """
    table, unparsed = parse_table_text(text)
    inferred: dict[str, dict] = {}
    if unparsed and backend is not None:
        inferred = interpret_labels(unparsed, backend)
        for row in table.rows:
            hit = inferred.get(row.age_label)
            if hit and hit["start"] is not None:
                end = hit["end"] if hit["end"] is not None else 200
                row.age_label = f"{hit['start']}-{end}" if hit["end"] is not None else f"{hit['start']}+"
                hit["applied_label"] = row.age_label
    _validate(table)
    # 查核完把原文標籤放回去顯示：畫面上要看得到「社會新鮮人 → 22-26（推定）」，
    # 而不是只剩一個像官方分組的 22-26。band 已經設好，對齊用的是 band 不是標籤。
    for row in table.rows:
        for x, v in inferred.items():
            if v.get("applied_label") == row.age_label:
                row.age_label = f"{x}（推定 {v['applied_label']}）"
                break
    if inferred:
        applied = [x for x, v in inferred.items() if v.get("applied_label")]
        if applied:
            table.notes += f"；{len(applied)} 個文字標籤由模型語意判讀成年齡區間（推定）：" + "、".join(
                f"{x}→{inferred[x]['applied_label']}" for x in applied)
    return table, inferred
