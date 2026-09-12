"""把散落的數字統整成一段人話。—— 命題：「透過 AI 統整…並整合出青年動態」

這是 RAG（retrieval-augmented generation）：先撈出相關資料，再讓模型看著它們寫。
但一般的 RAG 到「讓模型寫」就結束了 —— 模型還是會在敘述裡把數字寫錯或編出新的，
而且讀的人看不出來。

所以這裡多一步：**把模型寫出來的每一個數字，逐一比對回原始記錄。**

    撈  →  生成  →  驗證  →  對不上的標出來

驗證這一步是整個檔案存在的理由。它讓我們可以放心用 LLM 的表達力，
而不必接受它的編造 —— 兩者本來被認為只能二選一。

AI 在這裡做的仍然是「看懂並表達」，不是計算。所有數字都來自 engine/ 算好的記錄。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .backend import Backend

# 出現在敘述裡、但比對不到任何來源記錄的數字，就是編的。
# 容許小數點後的四捨五入差異（模型常把 59.9 寫成 59.90）。
TOLERANCE = 0.05

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 年齡區間（18-35、25～29）：兩邊都是標籤不是數據
_RANGE = re.compile(r"\d+\s*[-–~～]\s*\d+")


@dataclass
class Grounded:
    """一段敘述，以及它每個數字的查核結果。"""

    text: str
    records: list[dict] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    raw: str = ""
    steps: list[dict] = field(default_factory=list)   # 模型之前程式做了哪幾步（advise 會填），前端攤開給人看

    @property
    def trustworthy(self) -> bool:
        return not self.unverified

    def summary(self) -> str:
        total = len(self.verified) + len(self.unverified)
        if not total:
            return "敘述中沒有出現數字"
        if self.trustworthy:
            return f"{total} 個數字全部比對到來源記錄"
        # 對不上的數字只列一次（前端會另外用「模型估算」的措辭標出來，
        # 這裡再列會在同一行出現兩遍）
        return (f"{len(self.verified)}/{total} 個數字比對到來源，"
                f"{len(self.unverified)} 個對不上")


def _fmt(rec: dict) -> str:
    unit = rec.get("unit", "")
    v = rec["value"]
    if unit == "%":
        return f"{v * 100:.1f}%"
    if unit.startswith("萬"):
        return f"{v:.1f} {unit}"
    if unit == "倍":
        return f"{v:.2f} 倍"
    return f"{v:,.0f} {unit}"


def retrieve(payload: dict, region: str, band: str, limit: int = 24) -> list[dict]:
    """撈出跟這個地區、這個年齡層有關的記錄。

    刻意不做語意檢索 —— 資料是結構化的，欄位比對就夠精準，
    而且比對規則看得懂、查得到。向量檢索在這裡只會增加不確定性。
    """
    hits = [r for r in payload["records"]
            if r["region"] == region and r["age_group"] == band]
    hits += [r for r in payload["records"]
             if r["region"] == region and r["age_group"] not in (band,)
             and r["metric"] in {"平均年薪", "失業率", "就業者人數"}][:6]
    hits += [r for r in payload["records"] if r.get("education")][:6]
    # 居住負擔是家戶層級（age_group = 全體），沒有這一行模型永遠看不到它，
    # 問「青年買房」就只能說沒資料。標籤會寫明「非青年」，不會被當成青年數字。
    hits += [r for r in payload["records"]
             if r["region"] == region and r["age_group"] == "全體"]
    seen, out = set(), []
    for r in hits:
        key = (r["region"], r["age_group"], r["metric"], r.get("education"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:limit]


def build_prompt(payload: dict, records: list[dict], region: str, band: str,
                 question: str | None = None) -> str:
    lines = []
    for i, r in enumerate(records, 1):
        edu = f"／{r['education']}" if r.get("education") else ""
        # 「全體」的口徑照來源寫：家戶（居住）、申報戶（所得）、全體（普查工作機會）
        who = (f"{r['provenance'].get('source_age_group') or '全體'}（非青年）"
               if r["age_group"] == "全體" else f"{r['age_group']} 歲")
        lines.append(f"[{i}] {r['region']}{edu} {who} "
                     f"{r['metric']} = {_fmt(r)} "
                     f"（可靠度 {r['provenance']['confidence']}）")

    bench = payload.get("benchmark", {})
    bench_lines = []
    for metric, blk in bench.get("metrics", {}).items():
        rank = blk["rank"].get(region)
        if rank:
            bench_lines.append(f"- {metric}：六都第 {rank} / {len(bench.get('cities', []))}")

    trends = payload.get("trends", {}).get("salary", {})
    trend_line = ""
    if trends.get("years") and trends.get("mean", {}).get("25-29"):
        ys, vs = trends["years"], trends["mean"]["25-29"]
        trend_line = (f"25-29 歲平均年薪 {ys[0]} 年 {vs[0]} 萬 → "
                      f"{ys[-1]} 年 {vs[-1]} 萬")

    ask = question or f"請用三到四句話說明{region} {band} 歲青年的整體狀況。"

    return f"""你是一位統計分析助理。請根據下面的資料寫一段簡短的中文說明。

**最重要的規則：你只能使用下面列出的數字。**
不可以自己計算、不可以推估、不可以寫出清單裡沒有的數字。
需要比較大小、講趨勢方向是可以的，但任何具體數值都必須出自清單。
如果想講的東西清單裡沒有資料支持，就不要講。

可用的數字：
{chr(10).join(lines)}

六都排名（{region}）：
{chr(10).join(bench_lines) if bench_lines else '（無）'}

時間變化：
{trend_line or '（無）'}

任務：{ask}

寫作要求：
- 三到四句話，像寫給市府同仁看的摘要，不要條列
- 點出最值得注意的一件事，而不是把數字唸一遍
- 不要用「根據資料顯示」這種開場
- 直接輸出那段文字，不要任何前言或標題"""


def _numbers_in(text: str) -> list[str]:
    """抓出敘述裡真正屬於「數據」的數字。

    規則寫死而不是模糊比對 —— 模糊規則會把該查的放過去。
    先前用「附近有沒有出現某些字」判斷，結果「832,214 **名**青年」的
    那個「名」被當成「第 4 **名**」，整個數字就跳過不查了。

    跳過的四種：年齡區間兩端、西元年份、名次（第 N）、百分點差。
    其餘一律要能在來源記錄裡找到。
    """
    ranges = [(m.start(), m.end()) for m in _RANGE.finditer(text)]
    out: list[str] = []
    for m in _NUMBER.finditer(text):
        if any(a <= m.start() < b for a, b in ranges):
            continue                                  # 18-35 歲
        raw = m.group()
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        head = text[:m.start()].rstrip()
        tail = text[m.end():].lstrip()
        if head.endswith("第"):
            continue                                  # 排第 4
        if tail.startswith("歲"):
            continue                                  # 30 歲
        if tail.startswith("年") and 1900 <= val <= 2100:
            continue                                  # 2024 年
        if tail.startswith("個百分點"):
            continue                                  # 相差 7.4 個百分點
        if val <= 1:
            continue                                  # 0／1 多半是語氣
        # 條列編號：行首（允許前面有 markdown 記號）的「2.」「3、」「4)」。
        # 不擋的話，模型只要用編號清單回答，每個編號都會被報成「編造的數字」——
        # 誤報跟漏報一樣傷：讀的人一旦發現查核會冤枉正確的數字，
        # 之後真的抓到編造時他也不會信了。
        # 注意用未裁切的前綴。上面的 head 已經 rstrip() 過，換行符被吃掉了，
        # 拿它判斷「行首」永遠不會成立 —— 條列編號就漏出去了。
        line_head = text[:m.start()].rsplit("\n", 1)[-1].strip(" *-　")
        if line_head == "" and tail[:1] in (".", "、", ")", "．"):
            continue
        # 引用編號：advise() 的提示詞用 [1] [2] 標示每一筆記錄，模型會照著引
        # （例如「…佔 33%（[1-27]）」）。那是索引不是資料，報成編造會很難看。
        if head.endswith("[") or head.endswith("[") or tail.startswith("]"):
            continue
        if head.endswith("#") or head.endswith("＃"):
            continue                                  # 模型有時寫 #254 當引用編號
        out.append(raw)
    return out


def _is_percent(text: str, raw: str, nth: int) -> bool:
    """敘述裡第 nth 次出現的 raw 後面是不是接著 %。"""
    pos = -1
    for _ in range(nth + 1):
        pos = text.find(raw, pos + 1)
        if pos < 0:
            return False
    tail = text[pos + len(raw):].lstrip()
    return tail.startswith("%") or tail.startswith("％")


def _spellings(v: float, unit: str) -> list[tuple[float, bool]]:
    """一個值在敘述裡合理的寫法。**只做該單位會有的換算**。

    先前對每一筆都同時放 v、v×100、v÷1000 進池子：人口 47,183 人 ÷1000 = 47.2，
    於是模型編一個「47.2%」就對得上某個里的人口數。實測 0–100 之間任意一個
    百分比有七成能過關 —— 查核形同虛設。這支程式存在的理由就是抓編造的數字，
    池子鬆一點，整個主張就垮。

    每個寫法帶一個「是不是百分比」的旗標：敘述裡「47.2%」只准對上比率型的值，
    對上一個里的人口數不算。
    """
    if unit == "%":
        # 負的變化率（-0.04 = -4.0%）：模型寫「-4.0%」，數字擷取器抓到的是「4.0」（不含負號），
        # 所以正負兩種寫法都放進池子，不然年變化率永遠被報成編造
        out = [(round(v * 100, 1), True), (round(v * 100, 2), True), (v, False)]
        if v < 0:
            out += [(round(-v * 100, 1), True), (round(-v * 100, 2), True)]
        return out
    if unit.startswith("萬"):
        return [(v, False), (round(v, 1), False), (round(v * 10000), False)]   # 59.9 萬 → 599,000 元
    if unit == "倍":
        return [(v, False), (round(v, 1), False), (round(v, 2), False)]
    if unit in ("人", "個"):
        out = [(v, False), (round(v / 10000, 1), False)]           # 832,214 人 → 83.2 萬人
        if v < 0:                                                   # 淨遷入 −48 人：擷取器抓到的是 48
            out += [(-v, False), (round(-v / 10000, 1), False)]
        return out
    return [(v, False), (round(v, 1), False)]


def verify(text: str, records: list[dict], payload: dict | None = None,
           extra: list[str] | None = None) -> tuple[list, list]:
    """逐一比對敘述裡的數字是否真的來自記錄。

    這是整支程式的重點。模型寫得再流暢，只要有一個數字對不上，
    讀的人就無從分辨哪些可信 —— 所以要主動指出來。
    """
    pool: list[tuple[float, bool]] = []
    for r in records:
        pool += _spellings(r["value"], r.get("unit", ""))
    if payload:
        for blk in payload.get("benchmark", {}).get("metrics", {}).values():
            for v in blk.get("values", {}).values():
                pool += _spellings(v, blk.get("unit", ""))
        sal = payload.get("trends", {}).get("salary", {}).get("mean", {})
        for series in sal.values():
            pool += [(v, False) for v in series]
        # 施政建議與洞察卡片裡的數字。這些不是模型編的 —— 它們由
        # _policy_notes() 與 insights.detect() 用寫死的規則從記錄算出來，
        # 每張卡都附了依據。advise() 明確允許模型引用這些卡片，
        # 所以查核池也必須認得它們，否則會把「正確引用」報成「編造」。
        for card in list(payload.get("policy_notes") or []) + list(payload.get("insights") or []):
            text_bits = [card.get("title", ""), card.get("body", "")]
            text_bits += list(card.get("detail") or [])
            for bit in text_bits + list(extra or []):
                bit = str(bit)
                for m in _NUMBER.finditer(bit):
                    try:
                        pct = bit[m.end():].lstrip().startswith("%")
                        pool.append((float(m.group().replace(",", "")), pct))
                    except ValueError:
                        pass

    ok, bad = [], []
    seen: dict[str, int] = {}
    for raw in _numbers_in(text):
        val = float(raw.replace(",", ""))
        nth = seen.get(raw, 0)
        seen[raw] = nth + 1
        pct = _is_percent(text, raw, nth)
        # 寫成百分比的數字只能對上比率型的值；其餘寫法維持寬鬆
        if any(abs(val - p) <= max(TOLERANCE, abs(p) * 0.005) and (p_pct or not pct)
               for p, p_pct in pool):
            ok.append(raw)
        else:
            bad.append(raw)
    return ok, bad


def synthesize(payload: dict, backend: Backend, *, region: str = "新北市",
               band: str = "18-35", question: str | None = None) -> Grounded:
    """撈 → 生成 → 驗證。"""
    records = retrieve(payload, region, band)
    raw = backend.complete(build_prompt(payload, records, region, band, question))
    text = raw.strip()
    ok, bad = verify(text, records, payload)
    return Grounded(text=text, records=records, verified=ok, unverified=bad, raw=raw)
