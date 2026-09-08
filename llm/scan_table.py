"""讀掃描檔裡的統計表。—— 命題痛點第一條「格式不一（API／CSV／掃描檔）」

規則永遠讀不出一張圖片上的表格，這是 AI 才做得到的事。
但 AI 也是最會「看起來很有把握地講錯」的東西，所以這支程式的重點
不是呼叫模型，而是**呼叫完之後怎麼查它**。

三道查核，全部由我們的程式做，不問模型：

  1. 每個年齡標籤都要能被 AgeBand.parse() 解析
  2. 各年齡分組之間不能重疊
  3. **我們自己把每一列加起來，跟表上印的合計比對**

第 3 條是最有用的一條。掃描檔最常見的錯誤是某一位數字看錯（3 看成 8），
而那種錯誤一定會讓合計對不上。模型自己不會發現，但加法會。

AI 在這裡只做「轉錄」—— 把圖片上的字變成結構化資料。
它不做任何計算，也不做年齡對齊，那些交給 engine/。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.schema import AgeBand, MetricKind, SourceRecord  # noqa: E402

from .backend import Backend  # noqa: E402

# 合計對不上時的容忍度。官方統計表常有四捨五入造成的個位數落差，
# 但 OCR 看錯一位數字通常會差很多，兩者分得開。
TOTAL_TOLERANCE_RATIO = 0.01
TOTAL_TOLERANCE_ABS = 2.0

EXTRACT_PROMPT = """你是一個統計表轉錄工具。請把這張圖片上的統計表**原樣抄下來**。

嚴格規則：
1. 只轉錄你看得到的內容。**不要計算任何東西**，不要推算缺漏值，不要換算單位。
2. 看不清楚的地方，寫進 unreadable 陣列說明，**不要猜一個數字填上去**。
3. 年齡分組請照表上原文抄（例如「15～24歲」就寫 "15-24"，「未滿25歲」就寫 "未滿25"）。
4. 如果表上有印合計／總計，把它放進 printed_total；沒有就填 null。
5. kind 判斷：這一欄是人數之類可以相加的數量就填 "count"；
   是百分比、比率、平均值之類不能相加的就填 "rate"。

只輸出 JSON，不要任何其他文字：

{
  "metric": "這一欄統計的是什麼（例如 就業者人數）",
  "unit": "單位（例如 人、千人、%、萬元/年）",
  "region": "地區名稱，看不到就填 null",
  "year": 西元年份數字，看不到就填 null,
  "kind": "count 或 rate",
  "rows": [
    {"age_label": "15-24", "value": 123}
  ],
  "printed_total": 表上印的合計數字，沒有就 null,
  "unreadable": ["說明哪裡看不清楚"],
  "notes": "其他值得說明的事，沒有就空字串"
}"""


@dataclass
class ScannedRow:
    age_label: str
    value: float
    band: AgeBand | None = None       # 解析成功才有


@dataclass
class ScannedTable:
    """AI 從掃描檔轉錄出來的表，附上我們自己查核的結果。"""

    metric: str
    unit: str
    kind: MetricKind
    rows: list[ScannedRow]
    region: str | None = None
    year: int | None = None
    printed_total: float | None = None
    unreadable: list[str] = field(default_factory=list)
    notes: str = ""
    issues: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def computed_total(self) -> float | None:
        """我們自己加的合計。比率型不能相加，所以回傳 None。"""
        if self.kind is not MetricKind.EXTENSIVE:
            return None
        return sum(r.value for r in self.rows)

    @property
    def usable_rows(self) -> list[ScannedRow]:
        return [r for r in self.rows if r.band is not None]

    @property
    def trustworthy(self) -> bool:
        """三道查核全過，而且模型沒有回報看不清楚的地方。"""
        return not self.issues and not self.unreadable

    def summary(self) -> str:
        state = "全部查核通過" if self.trustworthy else f"{len(self.issues) + len(self.unreadable)} 項待確認"
        return (f"{self.region or '未標示地區'} {self.year or '未標示年份'} "
                f"{self.metric}（{self.unit}）· {len(self.rows)} 列 · {state}")


def _strip_fence(text: str) -> str:
    """模型有時會把 JSON 包在 ```json 圍欄裡。"""
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    return m.group(1) if m else text.strip()


def _validate(table: ScannedTable) -> None:
    """三道查核。全部由我們算，不問模型。"""
    seen: list[AgeBand] = []
    for row in table.rows:
        # 查核 1：年齡標籤要能解析
        try:
            row.band = AgeBand.parse(row.age_label)
        except ValueError:
            table.issues.append(
                f"年齡分組「{row.age_label}」無法解析，這一列不會進入對齊引擎"
            )
            continue
        # 查核 2：分組不能重疊
        for other in seen:
            if row.band.overlap(other):
                table.issues.append(
                    f"年齡分組「{row.age_label}」與「{other.label}」重疊 —— "
                    f"同一群人會被算兩次，可能是轉錄錯誤"
                )
                break
        seen.append(row.band)

        if row.value < 0:
            table.issues.append(f"「{row.age_label}」的數值是負的（{row.value}），不合理")

    # 查核 3：自己加一次，跟表上印的合計比對
    computed = table.computed_total
    if computed is not None and table.printed_total is not None:
        gap = abs(computed - table.printed_total)
        tolerance = max(TOTAL_TOLERANCE_ABS, table.printed_total * TOTAL_TOLERANCE_RATIO)
        if gap > tolerance:
            table.issues.append(
                f"⚠️ 合計對不上：表上印 {table.printed_total:,.0f}，"
                f"但各列加起來是 {computed:,.0f}（差 {gap:,.0f}）。"
                f"掃描檔最常見的錯誤就是看錯一位數字，請人工核對"
            )


def parse_response(text: str) -> ScannedTable:
    """把模型回的 JSON 轉成 ScannedTable 並查核。純函式，可離線測試。"""
    try:
        payload = json.loads(_strip_fence(text))
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型回的不是合法 JSON：{exc}") from exc

    kind_raw = str(payload.get("kind", "count")).lower()
    kind = MetricKind.EXTENSIVE if kind_raw == "count" else MetricKind.INTENSIVE

    rows: list[ScannedRow] = []
    for item in payload.get("rows", []):
        label = str(item.get("age_label", "")).strip()
        try:
            value = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue                       # 沒有數值的列直接跳過，下面會反映在合計上
        if label:
            rows.append(ScannedRow(age_label=label, value=value))

    if not rows:
        raise ValueError("模型沒有讀出任何一列資料")

    year = payload.get("year")
    table = ScannedTable(
        metric=str(payload.get("metric") or "未標示指標"),
        unit=str(payload.get("unit") or ""),
        kind=kind,
        rows=rows,
        region=payload.get("region") or None,
        year=int(year) if isinstance(year, (int, float)) else None,
        printed_total=(float(payload["printed_total"])
                       if payload.get("printed_total") is not None else None),
        unreadable=[str(u) for u in payload.get("unreadable", []) if str(u).strip()],
        notes=str(payload.get("notes") or ""),
        raw=text,
    )
    _validate(table)
    return table


def read_table(image_path: str | Path, backend: Backend) -> ScannedTable:
    """掃描檔 → 已查核的結構化表格。"""
    return parse_response(backend.complete(EXTRACT_PROMPT, image_path=image_path))


def to_source_records(
    table: ScannedTable,
    *,
    region: str | None = None,
    year: int | None = None,
    rate_key: str = "labor_force_participation",
) -> list[SourceRecord]:
    """轉成引擎吃的格式。

    到這裡 AI 的工作就結束了 —— 後面的年齡對齊、權重、信心度
    全部由 engine/ 的確定性公式處理。

    無法解析年齡的列會被丟掉（`_validate` 已經記在 issues 裡），
    不會靜靜混進去。
    """
    region = region or table.region
    year = year or table.year
    if not region or not year:
        raise ValueError(
            "掃描檔沒有標示地區或年份，也沒有由呼叫端指定 —— "
            "沒有這兩項無法建立可追溯的記錄"
        )

    note = f"由掃描檔經 AI 轉錄（{len(table.rows)} 列）"
    if table.issues or table.unreadable:
        note += f"；待確認 {len(table.issues) + len(table.unreadable)} 項"

    return [
        SourceRecord(
            region=region,
            year=year,
            age_band=row.band,
            metric=table.metric,
            value=row.value,
            unit=table.unit,
            kind=table.kind,
            source_agency="掃描檔（AI 判讀）",
            source_dataset=note,
            rate_key=rate_key if table.kind is MetricKind.EXTENSIVE else rate_key,
        )
        for row in table.usable_rows
    ]
