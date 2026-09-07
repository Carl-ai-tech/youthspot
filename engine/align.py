"""年齡層對齊引擎 —— Spec §5 的實作。

設計原則（§5.6）：**純函式，不做 I/O，不呼叫 LLM。**
數值一律由公式 A–D 決定；LLM 只在 llm_bridge.py 負責把非結構化的
年齡描述轉成 AgeBand，轉完就回到這裡。這樣就算 LLM 判斷有偏差，
也只影響區間對應，不會污染計算結果。

公式對照：
  A (T1)  加總型，人口權重          —— 誤差大，只在拿不到 r(a) 時用
  B (T2)  加總型，有效母體加權      —— 主力，誤差可壓到個位數
  C       比率型，分子分母各自插補  —— 同權重時會退化成組內同質假設
  D (T3)  比率型，benchmark 校準    —— 加分項，需要全國分齡結構
"""

from __future__ import annotations

from dataclasses import dataclass

from .reference import ReferenceData
from .schema import (
    AgeBand,
    AlignedRecord,
    Confidence,
    Method,
    MetricKind,
    Provenance,
    SourceRecord,
)

# 結構轉折點（§5.8）：切割點落在這些年齡上時，組內同質假設特別站不住腳。
# 18 歲是就學／就業分界，22 歲是大學畢業投入職場。
STRUCTURAL_BREAKS = frozenset({18, 22})

# 官方組值出現局部高峰／低谷時，組內藏著我們看不見的轉折，插補會失準。
# 門檻 10% 由 data/backtest.py 的回測定出：勞參率的翻轉幅度都在 0.5% 以下
# （實際拆得準），失業率在 20-24 歲翻轉 20.6%（實際拆出來比不拆更差）。
SHAPE_EXTREMUM_TOLERANCE = 0.10


@dataclass(slots=True)
class Weight:
    """一次插補的權重與它的來歷。"""

    value: float
    method: Method
    confidence: Confidence
    note: str


# ---------------------------------------------------------------------------
# 公式 A / B：加總型指標的權重
# ---------------------------------------------------------------------------

def weight_formula_a(
    ref: ReferenceData, source: AgeBand, target: AgeBand
) -> Weight:
    """公式 A（T1）：以總人口為基底。

            Σ(a = s'→e') P(a)
      w  =  ─────────────────────
            Σ(a = s →e ) P(a)

    §5.3 已經指出這個公式的問題：它假設組內每個年齡的發生機率相同。
    在就業類指標上會系統性低估年長端，誤差可達 20 個百分點以上。
    只有在拿不到分齡發生率 r(a) 時才用它，而且一律標低信心度。
    """
    overlap = source.overlap(target)
    if overlap is None:
        return Weight(0.0, Method.FORMULA_A_T1, Confidence.HIGH, "來源與目標區間無交集")

    denom = ref.population_sum(source)
    if denom <= 0:
        raise ValueError(f"來源區間 {source.label} 的人口總數為 0，無法計算權重")
    w = ref.population_sum(overlap) / denom

    notes = [f"人口權重 {ref.population_sum(overlap):,.0f}/{denom:,.0f}（單位：千人）"]
    if clip := ref.clip_note(source):
        notes.append(clip)
    notes.append("未使用分齡發生率，假設組內發生機率相同")

    return Weight(w, Method.FORMULA_A_T1, Confidence.LOW, "；".join(notes))


def weight_formula_b(
    ref: ReferenceData, source: AgeBand, target: AgeBand, rate_key: str
) -> Weight:
    """公式 B（T2）：以有效母體 P(a)·r(a) 為基底。

            Σ(a = s'→e') P(a)·r(a)
      w  =  ────────────────────────────
            Σ(a = s →e ) P(a)·r(a)

    把權重基底從「總人口」換成「該指標的有效母體」，成本只是多接一張
    分齡發生率表，卻能把就業類指標的誤差從 24% 降到個位數（§5.4）。
    """
    overlap = source.overlap(target)
    if overlap is None:
        return Weight(0.0, Method.FORMULA_B_T2, Confidence.HIGH, "來源與目標區間無交集")

    denom = ref.effective_population(rate_key, source)
    if denom <= 0:
        raise ValueError(
            f"來源區間 {source.label} 在曲線 {rate_key!r} 上的有效母體為 0"
        )
    numer = ref.effective_population(rate_key, overlap)
    w = numer / denom

    meta = ref.rate_meta(rate_key)
    notes = [f"有效母體加權 {numer:,.1f}/{denom:,.1f}（基底：{rate_key}）"]
    if clip := ref.clip_note(source):
        notes.append(clip)
    if meta.get("source"):
        notes.append(f"發生率來源：{meta['source']}")

    # §5.8 說「切割點跨越結構轉折 → 低信心度」。但那條規則是為 T1 寫的：
    # T1 用總人口當權重，完全看不見轉折，所以必須降級。
    # T2 用的 r(a) 曲線如果本身就在轉折處變化（例如 18 歲勞參率從 5% 跳到 40%），
    # 那轉折已經被吃進權重裡了，再降一次等於重複懲罰同一個問題。
    # 因此這裡改成檢查「r(a) 在切割點附近是否真的有變化」來決定要不要降級。
    conf = Confidence.MEDIUM
    cut_points = {
        p for p in (target.start, target.end + 1) if source.start < p <= source.end
    }
    for cut in sorted(cut_points):
        rev = ref.shape_reversal(rate_key, cut)
        if rev is not None and rev >= SHAPE_EXTREMUM_TOLERANCE:
            conf = Confidence.LOW
            notes.append(
                f"{rate_key} 在 {cut} 歲所屬的官方組別是局部高峰／低谷"
                f"（翻轉 {rev:.1%}），組內結構無法從公布值觀察，信心度降級"
            )

    for cut in sorted(cut_points & STRUCTURAL_BREAKS):
        if _rate_captures_break(ref, rate_key, cut, source):
            notes.append(
                f"切割點 {cut} 位於結構轉折，但 {rate_key} 曲線已反映該轉折，"
                f"不額外降級"
            )
        else:
            conf = Confidence.LOW
            notes.append(
                f"切割點 {cut} 跨越就學／就業結構轉折，"
                f"且 {rate_key} 曲線在此處平坦（未反映轉折），信心度降級"
            )

    return Weight(w, Method.FORMULA_B_T2, conf, "；".join(notes))


def best_weight(
    ref: ReferenceData, source: AgeBand, target: AgeBand, rate_key: str | None
) -> Weight:
    """§5.7 的決策樹：邊界吻合 → 直接對應；有 r(a) → 公式 B；否則公式 A。"""
    if source == target:
        return Weight(1.0, Method.EXACT_MATCH, Confidence.HIGH, "分組邊界完全吻合")
    if target.covers(source):
        # 來源整段都落在目標裡（例如拿 25-29 去組 25-35）：權重恰好是 1.0，
        # 一刀都沒切、沒有插補，沒有理由標成 medium。
        return Weight(
            1.0, Method.EXACT_MATCH, Confidence.HIGH,
            f"來源分組 {source.label} 完全落在目標 {target.label} 內，整筆納入，未插補",
        )
    if rate_key and ref.has_rate(rate_key):
        return weight_formula_b(ref, source, target, rate_key)
    note_prefix = ""
    if rate_key:
        note_prefix = f"找不到分齡發生率曲線 {rate_key!r}，退回 T1；"
    w = weight_formula_a(ref, source, target)
    return Weight(w.value, w.method, w.confidence, note_prefix + w.note)


# ---------------------------------------------------------------------------
# 加總型：把一或多筆來源記錄聚合到一個目標分組
# ---------------------------------------------------------------------------

def select_sources(
    sources: list[SourceRecord], target: AgeBand
) -> list[SourceRecord]:
    """從候選來源挑出一組互不重疊、而且盡量不用切開的組合。

    為什麼需要挑：同一個指標常常有好幾個機關發布，分組粗細不同。
    要 25-29 歲的時候，內政部的「25-29」直接對得上（信心度 high），
    主計總處的「25-44」卻要切開（medium）。挑細的那個是白賺的精度。

    另一個作用是**避免重複計算**：如果同時傳入 25-29 和 25-44，
    兩筆都算就會把同一群人加兩次。挑選時互相重疊的只留成本最低的那筆。

    成本排序：
      0  分組剛好等於目標          → 完全不用插補
      1  分組整段都在目標內        → 整筆納入，不用切
      2  分組跨出目標邊界          → 要切開，得插補
    同成本時優先選範圍窄的（分組較細，插補距離短）。
    """
    candidates = [s for s in sources if s.age_band.overlap(target)]

    def cost(s: SourceRecord) -> tuple[int, int]:
        band = s.age_band
        width = band.end - band.start
        if band == target:
            return (0, width)
        if target.covers(band):
            return (1, width)
        return (2, width)

    chosen: list[SourceRecord] = []
    for s in sorted(candidates, key=cost):
        if any(s.age_band.overlap(c.age_band) for c in chosen):
            continue
        chosen.append(s)
    return sorted(chosen, key=lambda s: s.age_band.start)


def align_extensive(
    ref: ReferenceData,
    sources: list[SourceRecord],
    target: AgeBand,
) -> AlignedRecord | None:
    """把與 target 有交集的來源插補後相加。

    目標分組可能需要由多筆來源拼起來（例如 18–35 要吃 15–24 和 25–44 兩筆），
    所以這裡是「逐筆算貢獻再加總」。哪幾筆要用，交給 select_sources 決定。
    """
    contributing = select_sources(sources, target)
    if not contributing:
        return None

    for s in contributing:
        if s.kind is not MetricKind.EXTENSIVE:
            raise ValueError(
                f"{s.metric} 是比率型指標，不能用 align_extensive —— "
                f"比率乘權重在統計上無意義（Spec §5.2）"
            )

    total = 0.0
    parts: list[str] = []
    weights: list[float] = []
    confidences: list[Confidence] = []
    methods: list[Method] = []

    for s in contributing:
        w = best_weight(ref, s.age_band, target, s.rate_key)
        total += s.value * w.value
        weights.append(w.value)
        confidences.append(w.confidence)
        methods.append(w.method)
        parts.append(f"{s.age_band.label}×{w.value:.4f}（{w.note}）")

    head = contributing[0]
    return AlignedRecord(
        region=head.region,
        year=head.year,
        age_group=target.label,
        gender=head.gender,
        metric=head.metric,
        value=round(total, 2),
        unit=head.unit,
        education=head.education,
        provenance=Provenance(
            source_agency=" / ".join(dict.fromkeys(s.source_agency for s in contributing)),
            source_dataset=" / ".join(dict.fromkeys(s.source_dataset for s in contributing)),
            source_age_group=" + ".join(s.age_band.label for s in contributing),
            method=_worst_method(methods),
            weight=round(sum(weights) / len(weights), 6),
            confidence=_worst_confidence(confidences),
            note="；".join(parts),
        ),
    )


# ---------------------------------------------------------------------------
# 公式 C：比率型，分子分母各自插補後相除
# ---------------------------------------------------------------------------

def align_ratio_formula_c(
    ref: ReferenceData,
    source: SourceRecord,
    target: AgeBand,
    numerator_rate_key: str | None = None,
    denominator_rate_key: str | None = None,
) -> AlignedRecord:
    """公式 C：先拆回定義，分子分母各自插補，最後再相除。

                          分子[s,e] × w_分子
      比率[s', e']  =  ──────────────────────────
                         分母[s,e] × w_分母

    ⚠️ 若 w_分子 == w_分母，權重會直接約掉，結果等於原始比率。
       這不是 bug —— 它在告訴你「你其實是在假設組內比率相同」（§5.5）。
       這種情況一律標成 low，並在 note 裡寫清楚。
    """
    if source.kind is not MetricKind.INTENSIVE:
        raise ValueError(f"{source.metric} 不是比率型指標")
    if source.numerator is None or source.denominator is None:
        raise ValueError(
            f"{source.metric} 缺少分子/分母原始數量，無法走公式 C。"
            f"比率型指標必須連同分子分母一起抓下來（Spec §5.5）"
        )
    if source.denominator <= 0:
        raise ValueError(f"{source.metric} 的分母為 0")

    w_num = best_weight(ref, source.age_band, target, numerator_rate_key)
    w_den = best_weight(ref, source.age_band, target, denominator_rate_key)

    num = source.numerator * w_num.value
    den = source.denominator * w_den.value
    if den <= 0:
        raise ValueError("插補後分母為 0，目標區間可能與來源無交集")
    value = num / den

    degenerate = abs(w_num.value - w_den.value) < 1e-12
    if degenerate:
        conf = Confidence.LOW
        note = (
            f"分子與分母使用相同權重 {w_num.value:.4f}，權重約分後結果等同原始比率 —— "
            f"這等於直接假設「組內比率相同」，未引入新資訊。"
            f"若要真正插補比率，需要外部分齡結構（公式 D）"
        )
    else:
        conf = _worst_confidence([w_num.confidence, w_den.confidence])
        note = (
            f"分子權重 {w_num.value:.4f}（{w_num.note}）；"
            f"分母權重 {w_den.value:.4f}（{w_den.note}）"
        )

    return AlignedRecord(
        region=source.region,
        year=source.year,
        age_group=target.label,
        gender=source.gender,
        metric=source.metric,
        value=round(value, 6),
        unit=source.unit,
        education=source.education,
        provenance=Provenance(
            source_agency=source.source_agency,
            source_dataset=source.source_dataset,
            source_age_group=source.age_band.label,
            method=Method.FORMULA_C,
            weight=round(w_num.value / w_den.value, 6),
            confidence=conf,
            note=note,
        ),
        extras={
            "_numerator": round(num, 2),
            "_denominator": round(den, 2),
        },
    )


# ---------------------------------------------------------------------------
# 公式 D（T3）：借用全國分齡結構當形狀先驗，再做 benchmark 校準
# ---------------------------------------------------------------------------

def align_ratio_formula_d(
    ref: ReferenceData,
    source: SourceRecord,
    target: AgeBand,
    shape_key: str,
    denominator_rate_key: str,
) -> AlignedRecord:
    """公式 D（T3）：iterative proportional fitting 的單步版本。

    步驟（§5.5）：
      1. 取全國分齡曲線 u_nat(a) 當「形狀」
      2. u_est(a) = u_nat(a) · k，k 待定
      3. 校準：令 Σ(a=s→e) 分母(a)·u_est(a) = 分子[s,e]，解出 k
      4. 用校準後的 u_est(a) 重新聚合到 [s', e']

    關鍵：k 是用**新北市自己的已知合計值**解出來的，所以借來的只有形狀，
    水準仍然錨定在本地資料上。
    """
    if source.numerator is None or source.denominator is None:
        raise ValueError(f"{source.metric} 缺少分子/分母原始數量，無法走公式 D")
    if not ref.has_rate(shape_key):
        raise ValueError(f"找不到形狀曲線 {shape_key!r}，無法走 T3")

    src_ages = [a for a in source.age_band.ages() if _has_all(ref, a, shape_key, denominator_rate_key)]
    if not src_ages:
        raise ValueError(f"來源區間 {source.age_band.label} 沒有可用的分齡資料")

    # 分母的分齡分布：用有效母體 P(a)·r(a) 當形狀，再縮放到已知合計值
    den_shape = {a: ref.population(a) * ref.rate(denominator_rate_key, a) for a in src_ages}
    den_shape_total = sum(den_shape.values())
    if den_shape_total <= 0:
        raise ValueError("分母的分齡形狀總和為 0")
    den_by_age = {
        a: source.denominator * v / den_shape_total for a, v in den_shape.items()
    }

    # 步驟 3：解 k
    implied = sum(den_by_age[a] * ref.rate(shape_key, a) for a in src_ages)
    if implied <= 0:
        raise ValueError("形狀曲線在來源區間上全為 0，無法校準")
    k = source.numerator / implied

    # 步驟 4：聚合到目標區間
    overlap = source.age_band.overlap(target)
    if overlap is None:
        raise ValueError(f"來源 {source.age_band.label} 與目標 {target.label} 無交集")
    tgt_ages = [a for a in overlap.ages() if a in den_by_age]

    num_t = sum(den_by_age[a] * ref.rate(shape_key, a) * k for a in tgt_ages)
    den_t = sum(den_by_age[a] for a in tgt_ages)
    if den_t <= 0:
        raise ValueError("目標區間插補後分母為 0")
    value = num_t / den_t

    # 借來的形狀如果本身在目標區間裡有局部高峰／低谷，代表官方組值裡藏著
    # 我們看不見的轉折 —— 回測顯示這種情況拆出來比不拆更差，必須降級。
    conf, shape_note = Confidence.MEDIUM, ""
    worst = max(
        ((ref.shape_reversal(shape_key, x) or 0.0), x) for x in overlap.ages()
    )
    if worst[0] >= SHAPE_EXTREMUM_TOLERANCE:
        conf = Confidence.LOW
        shape_note = (
            f"；⚠️ {shape_key} 在 {worst[1]} 歲所屬的官方組別是局部高峰／低谷"
            f"（翻轉 {worst[0]:.1%}），借來的形狀在此處不可靠，信心度降為 low"
        )

    meta = ref.rate_meta(shape_key)
    return AlignedRecord(
        region=source.region,
        year=source.year,
        age_group=target.label,
        gender=source.gender,
        metric=source.metric,
        value=round(value, 6),
        unit=source.unit,
        education=source.education,
        provenance=Provenance(
            source_agency=source.source_agency,
            source_dataset=source.source_dataset,
            source_age_group=source.age_band.label,
            method=Method.FORMULA_D_T3,
            weight=round(den_t / source.denominator, 6),
            confidence=conf,
            note=(
                f"benchmark 校準：借用 {shape_key} 的分齡形狀"
                f"（{meta.get('source', '來源未註明')}），"
                f"以本地合計值解出校準係數 k={k:.4f}；"
                f"形狀為借用值，水準錨定於本地資料" + shape_note
            ),
        ),
        extras={
            "_numerator": round(num_t, 2),
            "_denominator": round(den_t, 2),
            "_calibration_k": round(k, 6),
        },
    )


# ---------------------------------------------------------------------------
# 內部工具
# ---------------------------------------------------------------------------

_CONF_RANK = {
    Confidence.HIGH: 0,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 2,
    Confidence.INFERRED: 3,
}

_METHOD_RANK = {
    Method.EXACT_MATCH: 0,
    Method.FORMULA_B_T2: 1,
    Method.FORMULA_D_T3: 1,
    Method.FORMULA_C: 2,
    Method.FORMULA_A_T1: 3,
    Method.LLM_INFERRED: 4,
}


def _worst_confidence(items: list[Confidence]) -> Confidence:
    """多筆來源合成時，信心度取最差的那一個 —— 不要對外誇大精度。"""
    return max(items, key=lambda c: _CONF_RANK[c])


def _worst_method(items: list[Method]) -> Method:
    return max(items, key=lambda m: _METHOD_RANK[m])


# r(a) 在切割點兩側的比值超過這個倍數，就算「有反映結構轉折」。
# 1.5 是刻意保守的門檻：18 歲的 5%→40% 是 8 倍，輕鬆過關；
# 而純粹的統計雜訊不會造成 1.5 倍的落差。
BREAK_CAPTURE_RATIO = 1.5


def _rate_captures_break(
    ref: ReferenceData, rate_key: str, cut: int, source: AgeBand
) -> bool:
    """檢查發生率曲線在切割點附近是否有明顯落差。

    有落差 → T2 的權重已經把結構轉折算進去了，不需要再降信心度。
    """
    before, after = cut - 1, cut
    if before < source.start or after > source.end:
        return False
    try:
        r_before = ref.rate(rate_key, before)
        r_after = ref.rate(rate_key, after)
    except KeyError:
        return False
    lo, hi = sorted((r_before, r_after))
    if lo <= 0:
        return hi > 0
    return hi / lo >= BREAK_CAPTURE_RATIO


def _has_all(ref: ReferenceData, age: int, *rate_keys: str) -> bool:
    try:
        ref.population(age)
        for key in rate_keys:
            ref.rate(key, age)
    except KeyError:
        return False
    return True
