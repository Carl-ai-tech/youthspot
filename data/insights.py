"""異常與洞察偵測。—— Spec P1-3「自動標出資料中的顯著變化」

**不是讓 AI 找洞察。** 跟 `_policy_notes()` 同一個立場：規則寫死、依據附上、
點得開查得到。模型很會寫「值得注意的是…」，但它挑出來的東西不見得在統計上
站得住，而且沒有人查得出來它是怎麼挑的。

跟施政建議的分工：
    施政建議  規範性 —— 「應該把資源投在哪」
    異常洞察  描述性 —— 「哪裡發生了統計上顯著的變化」
兩者刻意分開，因為前者要背責任，後者只要對數字誠實。

⚠️ **命題舉的例子（「30–35 歲女性勞參率在 2023 年後下降」）做不出來** ——
官方的**分齡**勞參率、失業率、薪資表都沒有性別欄位。手上帶性別的只有兩樣：
戶政司的單一年齡人口（分齡 × 性別，實數）與新北市的全年齡勞參率（男／女序列）。
所以能檢定的是跨年齡組、跨行業、以及**全年齡**的男女勞參率變化。
這件事要主動講，不要讓評審自己發現。

四個偵測器，全部建立在 `forecast.fit()` 的最小平方 + t 檢定上，
沒有另外發明統計方法：

    1. deviation    最新一年掉出「用先前資料畫的」95% 預測區間 → 真正的異常
    2. recent_shift 近幾年的斜率跟更早的斜率反向          → 「在某年之後轉向」
    3. trend        整段序列斜率顯著不為零                → 長期方向
    4. divergence   同一指標在不同年齡組之間的成長率落差   → 青年 vs 中壯年

純函式，不碰網路、不碰檔案，可單獨測試。
"""

from __future__ import annotations

from . import forecast

# deviation 要留最後一點出來驗證，前面至少要 4 點才 fit 得動（fit 本身要 3 點）
MIN_FOR_DEVIATION = 5
# deviation 只用最近這麼多年外推。先前用整段 47 年：15–19 歲勞參率的長期直線外推到
# 2025 年變成 −0.01%、25–29 歲女性 99.8% —— 曲線早就趨平，直線還一路走，於是
# 「偏離」全是假警報，還跟同一頁的圖說矛盾。趨勢是局部的，外推也該是局部的。
DEVIATION_WINDOW = 15
# recent_shift 前後各要 fit 一次，兩邊都要 3 點以上
RECENT_WINDOW = 5
MIN_FOR_SHIFT = RECENT_WINDOW + 3
# 同一組指標的成長率差距要到這個倍數才算「落差」，否則只是雜訊
DIVERGENCE_RATIO = 1.8
# 迴歸解釋力低於這條線，就只講方向不講「持續」，並降為 low
R2_SOLID = 0.50
MAX_CARDS = 8
# 同一張圖最多幾張卡（見 detect 結尾）
MAX_PER_CHART = 2


# ── 單位格式化 ────────────────────────────────────────────
# rate 是小數（0.0342 = 3.42%），money 是萬元/年，count 是千人

def _fmt(value: float, kind: str) -> str:
    if kind == "rate":
        return f"{value:.2%}"
    if kind == "money":
        return f"{value:.1f} 萬"
    if kind == "count":
        return f"{value:,.0f} 千人"
    if kind == "times":
        return f"{value:,.2f} 倍"
    return f"{value:,.1f}"


def _fmt_delta(value: float, kind: str) -> str:
    """差距要帶正負號；比率型用百分點，不要跟百分比混淆。"""
    if kind == "rate":
        return f"{value * 100:+.2f} 個百分點"
    if kind == "money":
        return f"{value:+.1f} 萬"
    if kind == "count":
        return f"{value:+,.0f} 千人"
    if kind == "times":
        return f"{value:+,.2f} 倍"
    return f"{value:+,.1f}"


def _confidence(trend: forecast.Trend) -> str:
    """沿用 forecast 的判定 —— 點數不足或斜率不顯著就是 low，永遠不給 high。"""
    return trend.confidence


class Series:
    """一條要被掃描的序列。

    `youth` 決定它能不能單獨成為一張卡。這是青年儀表板，
    「40-49 歲薪資在成長」不是洞察，是雜訊 —— 但那條序列仍然要留著，
    因為組間比較需要它當對照組。
    """

    __slots__ = ("name", "kind", "points", "source", "scope", "youth", "metric", "chart")

    def __init__(self, name, kind, years, values, source, scope="", youth=True, metric="",
                 chart=""):
        self.name, self.kind, self.source, self.scope = name, kind, source, scope
        self.youth = youth
        # 這條序列畫在儀表板的哪張圖上。卡片帶著這個鍵，前端就能做「看圖 →」。
        self.chart = chart
        # 指標名稱單獨存，合併時要靠它判斷「這幾條是不是在講同一件事」。
        # 從 name 拆字串猜會在改文案時無聲失效。
        self.metric = metric or name
        self.points = [(float(y), float(v)) for y, v in zip(years, values)
                       if v is not None]


# ── 偵測器 1：偏離既有趨勢 ────────────────────────────────

def _deviation(s: Series) -> dict | None:
    """最新一年的實際值，有沒有掉出「先前資料外推」的 95% 預測區間？

    這才是嚴格意義的「異常」：不是漲跌，是**漲跌超出既有趨勢能解釋的範圍**。
    刻意用 fit(前面) → predict(最後一年) 而不是 fit(全部)，
    因為把要檢驗的那點也放進迴歸會把它自己拉進區間裡，等於自己驗自己。
    """
    if len(s.points) < MIN_FOR_DEVIATION:
        return None
    hist, (last_x, last_y) = s.points[-DEVIATION_WINDOW - 1:-1], s.points[-1]
    tr = forecast.fit(hist)
    if tr is None:
        return None
    yhat, margin = tr.predict(last_x)
    dev = last_y - yhat
    # 硬性檢查：比率型的外推值或區間跨出 0–100%，這條外推本身就不成立，不報
    if s.kind == "rate" and not (0.0 <= yhat - margin and yhat + margin <= 1.0):
        return None

    # 先前資料完全落在一條直線上時殘差為零，區間寬度也是零。
    # 那是**最有把握**的情況，不是最沒把握的 —— 直接用 `margin <= 0` 當
    # 「驗不出來」會把最明顯的異常靜靜放掉（forecast.t_stat 的 docstring
    # 警告的是同一個陷阱）。真實資料很少這麼乾淨，但邊界不能靠運氣。
    if margin <= 0:
        if abs(dev) < 1e-9:
            return None                 # 完美延續，本來就不是異常
        score = float("inf")
    else:
        if abs(dev) <= margin:
            return None
        score = abs(dev) / margin

    year = int(last_x)
    word = "高於" if dev > 0 else "低於"
    span = (f"{_fmt(yhat - margin, s.kind)}–{_fmt(yhat + margin, s.kind)}"
            if margin > 0 else f"剛好 {_fmt(yhat, s.kind)}")
    # 與區間邊界的差距（永遠是正數；擦邊的要講明）
    edge = abs(dev) - margin if margin > 0 else abs(dev)
    edge_word = "接近邊界，" if margin > 0 and edge < margin * 0.1 else ""
    return {
        "_kind": "deviation",
        "_score": score,
        "_year": year,
        "_sign": 1 if dev > 0 else -1,
        "_series": s,
        "_dev": dev,
        "title": f"{s.name}在 {year} 年偏離既有趨勢",
        "body": f"{s.scope}{s.name} {year} 年實際為 {_fmt(last_y, s.kind)}，"
                f"但用 {int(hist[0][0])}–{int(hist[-1][0])} 年的趨勢外推應該落在 "
                f"{span}：比點預測{'高' if dev > 0 else '低'} {_fmt_delta(abs(dev), s.kind).lstrip('+')}，"
                f"比區間{'上' if dev > 0 else '下'}限{'高' if dev > 0 else '低'} {_fmt_delta(edge, s.kind).lstrip('+')}（{edge_word}已超出 95% 區間）。"
                "偏離的是趨勢本身，不只是漲跌。",
        "detail": [
            f"趨勢外推預測　{_fmt(yhat, s.kind)}"
            + (f"（95% 區間 ±{_fmt(margin, s.kind)}）" if margin > 0
               else "（先前各點完全在一條直線上，區間為零）"),
            f"實際觀測值　　{_fmt(last_y, s.kind)}",
            f"用來外推的年度　{int(hist[0][0])}–{int(hist[-1][0])}，共 {len(hist)} 點",
        ],
        "confidence": _confidence(tr),
        "basis": f"{s.source}；最小平方外推 + 95% 預測區間（不含被檢驗的該年）",
    }


# ── 偵測器 2：近期轉向 ────────────────────────────────────

def _recent_shift(s: Series) -> dict | None:
    """近 5 年的斜率，跟更早那段的斜率，方向相反嗎？

    命題舉的例子是「在 2023 年後下降」—— 那是轉向，不是偏離單點。
    要求**兩段都各自顯著**才報，否則平坦序列的隨機抖動會被講成轉折。
    """
    if len(s.points) < MIN_FOR_SHIFT:
        return None
    early, recent = s.points[:-RECENT_WINDOW], s.points[-RECENT_WINDOW:]
    a, b = forecast.fit(early), forecast.fit(recent)
    if a is None or b is None:
        return None
    if not a.significant or not b.significant:
        return None
    if (a.slope > 0) == (b.slope > 0):
        return None

    turn = int(recent[0][0])
    was = "上升" if a.slope > 0 else "下降"
    now = "下降" if a.slope > 0 else "上升"
    return {
        "_kind": "shift",
        "_score": abs(b.t_stat),
        "_series": s,
        "title": f"{s.name}在 {turn} 年前後由{was}轉為{now}",
        "body": f"{s.scope}{s.name} 在 {int(early[0][0])}–{int(early[-1][0])} 年"
                f"每年{was} {_fmt_delta(a.slope, s.kind)}，"
                f"但 {turn}–{int(recent[-1][0])} 年變成每年{now} "
                f"{_fmt_delta(b.slope, s.kind)}。兩段的斜率各自都通過顯著性檢定，"
                "所以這是方向改變，不是雜訊。",
        "detail": [
            f"{int(early[0][0])}–{int(early[-1][0])}　每年 {_fmt_delta(a.slope, s.kind)}",
            f"{turn}–{int(recent[-1][0])}　每年 {_fmt_delta(b.slope, s.kind)}",
            f"最新值　{_fmt(s.points[-1][1], s.kind)}",
        ],
        "confidence": _confidence(b),
        "basis": f"{s.source}；前後兩段分別做最小平方迴歸，兩段斜率都需通過雙尾 5% 檢定",
    }


# ── 偵測器 3：長期方向 ────────────────────────────────────

def _trend(s: Series) -> dict | None:
    """整段序列的斜率顯著不為零 —— 最基本的「這個指標在動」。

    ⚠️ 顯著 ≠ 解釋力強。序列夠長時（新北市勞動力有 19 點），
    一條很雜的線也能通過顯著性檢定：實測失業率 R² 只有 0.27，
    卻照樣顯著。這種情況說「持續下降」是超譯 —— 趨勢只解釋了
    27% 的變異，其餘都是波動。所以 R² 低就改口徑、並降為 low。
    """
    tr = forecast.fit(s.points)
    if tr is None or not tr.significant:
        return None
    word = "上升" if tr.slope > 0 else "下降"
    y0, y1 = int(s.points[0][0]), int(s.points[-1][0])
    weak = tr.r2 < R2_SOLID
    return {
        "_kind": "trend",
        "_score": abs(tr.t_stat),
        "_series": s,
        "title": f"{s.name}在 {y0}–{y1} 年"
                 + (f"整體{word}，但年度間波動大" if weak else f"持續{word}"),
        "body": f"{s.scope}{s.name} 平均每年{word} {_fmt_delta(tr.slope, s.kind)}，"
                f"{y1} 年為 {_fmt(s.points[-1][1], s.kind)}。"
                + (f"斜率通過雙尾 5% 顯著性檢定，但 R² 只有 {tr.r2:.2f}"
                   "—— 方向可信，幅度不可信，逐年波動遠大於趨勢本身。"
                   if weak else
                   f"斜率通過雙尾 5% 顯著性檢定，R²={tr.r2:.2f}。"),
        "detail": [
            f"{y0} 年　{_fmt(s.points[0][1], s.kind)}",
            f"{y1} 年　{_fmt(s.points[-1][1], s.kind)}",
            f"每年變化　{_fmt_delta(tr.slope, s.kind)}　R²={tr.r2:.2f}　n={tr.n}",
        ],
        # 解釋力不足時不給 medium，即使檢定過了
        "confidence": "low" if weak else _confidence(tr),
        "basis": f"{s.source}；最小平方線性迴歸，斜率雙尾 5% 檢定"
                 + ("；R² 偏低，僅代表方向" if weak else ""),
    }


# ── 偵測器 4：組間落差 ────────────────────────────────────

def _divergence(group: list[Series], label: str) -> dict | None:
    """同一個指標，不同年齡組的成長率差多少？

    這是給青年儀表板用的 —— 「青年的薪資成長跟不上中壯年」比
    「薪資在成長」有意義得多。只比成長率，不比水準：
    水準差是年資造成的，成長率差才是結構問題。
    """
    rates = []
    for s in group:
        tr = forecast.fit(s.points)
        if tr is None or not tr.significant or not tr.last_y:
            continue
        rates.append((s, tr, tr.slope / tr.last_y))
    if len(rates) < 2:
        return None

    rates.sort(key=lambda t: t[2])
    slow_s, slow_t, slow_r = rates[0]
    fast_s, fast_t, fast_r = rates[-1]
    if slow_r <= 0 or fast_r / slow_r < DIVERGENCE_RATIO:
        return None

    return {
        "_kind": "divergence",
        "_score": fast_r / slow_r,
        "_series": fast_s,
        "title": f"{label}的成長速度在年齡組之間差了 {fast_r / slow_r:.1f} 倍",
        "body": f"{fast_s.name}每年成長 {fast_r:+.1%}，"
                f"{slow_s.name}只有 {slow_r:+.1%}。"
                "比的是成長率不是水準。可能原因之一（待驗證）是同期基本工資的調漲，"
                "對低薪的年輕組影響較大 —— 這對青年是好消息；是否為結構性改變，要更長的序列才能判斷。",
        "detail": [f"{s.name}　每年 {r:+.1%}　{int(s.points[-1][0])} 年 "
                   f"{_fmt(s.points[-1][1], s.kind)}"
                   for s, _, r in sorted(rates, key=lambda t: -t[2])],
        "confidence": "medium" if min(slow_t.n, fast_t.n) >= forecast.MIN_POINTS else "low",
        "basis": f"{fast_s.source}；各組分別做最小平方迴歸，"
                 f"只納入斜率顯著的組別，比較相對於最新值的年成長率",
    }


# ── 組裝 ──────────────────────────────────────────────────

def _merge_deviations(cards: list[dict]) -> list[dict]:
    """同一年、同方向的偏離要合併成一張卡。

    五個年齡組在 2024 年同時高於趨勢，那是**一個**現象（很可能是共同外因，
    例如基本工資調整），不是五個。拆成五張卡會洗版，而且會讓人以為
    找到五件事。

    合併還會**提高**信心度：單一序列偏離可能是雜訊，多條獨立序列
    往同一個方向偏離則互相佐證。這是合併後才成立的證據，不是灌水。
    """
    groups: dict[tuple[int, int, str], list[dict]] = {}
    for c in cards:
        # 指標也要一致。先前只看（年份, 方向），結果把「失業率偏低」和
        # 「勞參率偏低」併成同一個現象，還宣稱它們有共同外因 —— 那是兩件事。
        groups.setdefault((c["_year"], c["_sign"], c["_series"].metric), []).append(c)

    out: list[dict] = []
    for (year, sign, metric), items in groups.items():
        if len(items) == 1:
            out.append(items[0])
            continue

        items.sort(key=lambda c: -c["_score"])
        word = "高於" if sign > 0 else "低於"
        kind = items[0]["_series"].kind
        names = "、".join(c["_series"].name for c in items)
        scope = items[0]["_series"].scope
        out.append({
            "_kind": "deviation",
            "_score": max(c["_score"] for c in items),
            "_series": items[0]["_series"],
            "title": f"{len(items)} 個年齡組的{metric}在 {year} 年同時{word}既有趨勢",
            "body": f"{scope}{names} 的 {year} 年實際值，全部落在各自趨勢外推的 "
                    f"95% 預測區間之外，而且方向一致（都{word}）。"
                    f"同一個指標的多個年齡組同時往同一個方向偏離，"
                    "通常指向影響整體的外部因素，而不是各年齡層各自的變化。",
            "detail": [f"{c['_series'].name}　實際 {_fmt(c['_series'].points[-1][1], kind)}"
                       f"　趨勢外推應為 {_fmt(c['_series'].points[-1][1] - c['_dev'], kind)}"
                       f"（{_fmt_delta(c['_dev'], kind)}）" for c in items],
            # 三條以上互相佐證才升級；兩條可能只是巧合
            "confidence": "medium" if len(items) >= 3 else items[0]["confidence"],
            "basis": items[0]["basis"] + f"；{len(items)} 條序列獨立檢定後方向一致",
        })
    return out


def _series_from_trends(trends: dict, region: str) -> tuple[list[Series], list[Series]]:
    """把 unified.json 的 trends 攤成可掃描的序列。

    回傳 (全部序列, 薪資年齡組序列) —— 後者另外拿去做組間比較。
    """
    all_series: list[Series] = []
    salary_bands: list[Series] = []

    sal = trends.get("salary") or {}
    years = sal.get("years") or []
    src = sal.get("source", "")
    for band, values in (sal.get("median") or {}).items():
        # 表6 的分組是 未滿25 / 25-29 / 30-39 / 40-49 / 50-64。
        # 前三組涵蓋 18–35 歲，後兩組只當對照組。
        youth = band in ("未滿25", "25-29", "30-39")
        s = Series(f"{band} 歲中位數年薪", "money", years, values, src, region, youth,
                   metric="中位數年薪", chart="salary")
        all_series.append(s)
        salary_bands.append(s)

    # 全國分齡序列（1978– ）。這是唯一同時「夠長」又「分齡」的資料：
    # 薪資只有 6 年，新北市在地序列雖有 19 年卻是全年齡。
    # 偵測器的檢定力直接受點數影響，48 點跟 6 點不是同一個等級。
    nat = trends.get("national") or {}
    nyears = nat.get("years") or []
    nsrc = nat.get("source", "")
    for key, label, kind in (("lfpr", "勞參率", "rate"), ("unemployment", "失業率", "rate")):
        for band, values in (nat.get(key) or {}).items():
            all_series.append(Series(
                f"{band} 歲{label}", kind, nyears, values, nsrc,
                # 全國不是新北市，講的時候一定要標出來
                scope="全國", youth=True, metric=label, chart=f"national-{key}",
            ))

    # 分齡 × 性別（全國）。命題 §4.2 的例子就是這種序列 —— 現在掃得到了。
    for sex, word in (("f", "女性"), ("m", "男性")):
        for key, label in (("lfpr", "勞參率"), ("unemployment", "失業率")):
            for band, values in (nat.get(f"{key}_{sex}") or {}).items():
                all_series.append(Series(
                    f"{band} 歲{word}{label}", "rate", nyears, values, nat.get("sex_source", nsrc),
                    scope="全國", youth=True, metric=f"{word}{label}", chart=f"national-{key}",
                ))

    lab = trends.get("labour") or {}
    lyears = lab.get("years") or []
    lsrc = lab.get("source", "")
    # 這條是全年齡的，講的時候要標清楚，不要讓人以為是青年的數字
    scope = f"{region}（全年齡）"
    for key, name, kind in (
        ("lfpr", "勞動力參與率", "rate"),
        ("unemployment", "失業率", "rate"),
        ("employed", "就業者人數", "count"),
        ("lfpr_f", "女性勞動力參與率", "rate"),
        ("lfpr_m", "男性勞動力參與率", "rate"),
    ):
        vals = lab.get(key)
        if vals and any(v for v in vals):
            all_series.append(Series(name, kind, lyears, vals, lsrc, scope,
                                     metric=name, chart="labour"))

    # 居住負擔是季資料。偵測器的文案全部以「年」為單位（recent_shift 的窗口是
    # 5 個點、deviation 講「最新一年」），直接餵 97 季會變成「近 5 季」卻寫成年。
    # 所以先合成年平均，只取四季齊全的年份 —— 半年的平均會被季節性拉偏。
    hou = trends.get("housing") or {}
    hsrc = hou.get("source", "")
    for metric, kind, scale in (("房價所得比", "times", 1), ("貸款負擔率", "rate", 100)):
        vals = (hou.get("series") or {}).get(metric, {}).get(region)
        if not vals:
            continue
        ys, vs = _annual_mean(hou.get("quarters") or [], vals)
        all_series.append(Series(
            metric, kind, ys, [v / scale for v in vs], hsrc,
            # 家戶層級，不是青年 —— 卡片文字裡一定要看得到這件事
            scope=f"{region}（全體家戶，非青年）", youth=True, metric=metric, chart="housing",
        ))

    return all_series, salary_bands


def _annual_mean(quarters: list[str], values: list[float]) -> tuple[list[int], list[float]]:
    """'104Q1'… 的季序列 → 只含四季齊全年份的年平均。民國轉西元。"""
    by_year: dict[int, list[float]] = {}
    for q, v in zip(quarters, values):
        y = int(q.upper().split("Q")[0]) + 1911
        by_year.setdefault(y, []).append(v)
    years = sorted(y for y, qs in by_year.items() if len(qs) == 4)
    return years, [sum(by_year[y]) / 4 for y in years]


def _touches_youth(card: dict) -> bool:
    """卡片講的序列有沒有碰到 18–35 歲？從序列名稱裡的年齡區間判斷。"""
    import re
    names = [card.get("title", "")]
    ser = card.get("_series")
    if ser is not None:
        names.append(getattr(ser, "name", ""))
    names += [d for d in card.get("detail") or []]
    for text in names:
        for lo, hi in re.findall(r"(\d{2})-(\d{2})", text):
            if int(hi) >= 18 and int(lo) <= 35:
                return True
        if "未滿25" in text or "18-35" in text or "全年齡" in text:
            return True
    return False


def detect(trends: dict, region: str = "新北市") -> list[dict]:
    """掃描所有序列，回傳依重要性排序的洞察卡片。

    排序刻意不是單純按 `_score` —— 不同偵測器的分數尺度不一樣，比不得。
    改成先照「這種發現有多值得注意」分類排，類別內才按分數。
    """
    all_series, salary_bands = _series_from_trends(trends, region)

    devs: list[dict] = []
    others: list[dict] = []
    for s in all_series:
        if len(s.points) < 3:
            continue
        # 偏離要掃全部序列 —— 非青年組雖然不單獨成卡，但它們一起偏離
        # 正是「共同外因」的證據，合併時要算進去。
        card = _deviation(s)
        if card:
            devs.append(card)
            continue
        # 趨勢與轉向只報青年組，否則「40-49 歲薪資在成長」會洗掉真正的發現
        if not s.youth:
            continue
        # 一條序列只報最強的一種發現，避免同一件事被講兩次
        for detector in (_recent_shift, _trend):
            card = detector(s)
            if card:
                others.append(card)
                break

    found = _merge_deviations(devs) + others

    div = _divergence(salary_bands, "中位數年薪")
    if div:
        found.append(div)

    # 排序：先看發現的種類有多值得注意，同類再看信心度，最後才比強度。
    # 信心度擺在強度前面是刻意的 —— 一個 low 的強偏離不該壓過
    # 一個 medium 的結構性落差，那會讓畫面第一眼全是不可靠的東西。
    priority = {"deviation": 0, "shift": 1, "divergence": 2, "trend": 3}
    conf_rank = {"high": 0, "medium": 1, "low": 2}
    # 這是青年儀表板：講到 18–35 歲的卡排前面。全國分齡序列的 15–19 歲（未成年）、
    # 薪資的 40–49／50–64 歲都只是對照組，不該佔前兩張。
    found.sort(key=lambda c: (0 if _touches_youth(c) else 1,
                              priority.get(c["_kind"], 9),
                              conf_rank.get(c["confidence"], 9),
                              -c["_score"]))

    for c in found:
        ser = c.get("_series")
        if ser is not None and getattr(ser, "chart", ""):
            c["chart"] = ser.chart
        for k in ("_score", "_year", "_sign", "_series", "_dev"):
            c.pop(k, None)

    # 同一張圖最多出 MAX_PER_CHART 張卡。48 年的全國序列點數多、檢定力強，
    # 不設上限時八張卡有六張都是它 —— 居住負擔那兩條 24 年的趨勢永遠擠不進來，
    # 第四個資料來源就在儀表板上消失了。先按上限挑，有剩的名額再回頭補。
    picked, spill, per_chart = [], [], {}
    for c in found:
        key = c.get("chart", "")
        if per_chart.get(key, 0) < MAX_PER_CHART:
            per_chart[key] = per_chart.get(key, 0) + 1
            picked.append(c)
        else:
            spill.append(c)
    return (picked + spill)[:MAX_CARDS]
