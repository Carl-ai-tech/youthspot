"""把五歲組的比率拆成單一年齡的比率（人口統計上叫 ungrouping / graduation）。

為什麼需要這個：
    主計總處的勞參率最細只到「15-19 歲」，但我們的切點在 18 歲 ——
    正好卡在那一組的肚子裡。不拆開就看不到 18 歲那道坎，
    公式 B 會退化成一條平線，信心度被降到 low。

怎麼拆（兩步）：
    1. 形狀：用組中點當節點跑 PCHIP（保單調的三次 Hermite 插值）。
       選 PCHIP 不選一般三次樣條，是因為樣條會 overshoot ——
       勞參率從 9.6% 衝到 63% 那一段，樣條會先掉到負的再彈上去。
    2. 校準：把每一組內部等比例縮放，讓「加權平均」精確等於官方公布值。

    第 2 步是這整個做法能不能上台的關鍵：**不管形狀猜得多爛，
    每一組的加總永遠等於官方數字**。我們只在組內重新分配，不創造總量。
    所以誤差被關在組內，而且只影響切點落在組中間的那些指標。

權重要用對：
    勞參率的組值 = Σ人口·勞參 / Σ人口       → 權重是 P(a)
    失業率的組值 = Σ失業 / Σ勞動力          → 權重是 P(a)·勞參率(a)
    用錯權重，校準出來的曲線就對不回官方值。呼叫端要自己傳對。
"""

from __future__ import annotations

from bisect import bisect_right

Band = tuple[int, int]


# ---------------------------------------------------------------- PCHIP


def _pchip_slopes(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch–Carlson 斜率：保證插出來的曲線不會在節點之間亂衝。"""
    n = len(xs)
    if n == 1:
        return [0.0]
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    if n == 2:
        return [d[0], d[0]]

    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0          # 轉折點，壓平，避免 overshoot
        else:
            w1 = 2 * h[i] + h[i - 1]
            w2 = h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    m[0] = _end_slope(h[0], h[1], d[0], d[1])
    m[-1] = _end_slope(h[-1], h[-2], d[-1], d[-2])
    return m


def _end_slope(h0: float, h1: float, d0: float, d1: float) -> float:
    m = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
    if m * d0 <= 0:
        return 0.0
    if d0 * d1 <= 0 and abs(m) > abs(3 * d0):
        return 3 * d0
    return m


def _hermite(xs: list[float], ys: list[float], ms: list[float], x: float) -> float:
    """節點外用端點斜率做線性外推；外推出來的負值由呼叫端夾掉。"""
    if x <= xs[0]:
        return ys[0] + ms[0] * (x - xs[0])
    if x >= xs[-1]:
        return ys[-1] + ms[-1] * (x - xs[-1])
    i = bisect_right(xs, x) - 1
    h = xs[i + 1] - xs[i]
    t = (x - xs[i]) / h
    t2, t3 = t * t, t * t * t
    return (
        (2 * t3 - 3 * t2 + 1) * ys[i]
        + (t3 - 2 * t2 + t) * h * ms[i]
        + (-2 * t3 + 3 * t2) * ys[i + 1]
        + (t3 - t2) * h * ms[i + 1]
    )


# ---------------------------------------------------------------- 主函式


def ungroup(
    bands: dict[Band, float],
    weight: dict[int, float],
    *,
    cap: float = 1.0,
) -> dict[int, float]:
    """五歲組比率 → 單一年齡比率。

    bands   {(15, 19): 0.0957, (20, 24): 0.6323, ...}  官方公布的組別比率
    weight  {age: w}  校準用的權重（勞參率用 P(a)，失業率用 P(a)·勞參率）
    cap     比率上限，預設 1.0

    回傳的曲線保證：每一組的加權平均 == 官方公布值（誤差在浮點精度內）。
    """
    if not bands:
        return {}

    ordered = sorted(bands)
    xs = [(lo + hi) / 2 for lo, hi in ordered]     # 組中點當節點
    ys = [float(bands[b]) for b in ordered]
    ms = _pchip_slopes(xs, ys)

    out: dict[int, float] = {}
    for (lo, hi), target in zip(ordered, ys):
        ages = [a for a in range(lo, hi + 1) if weight.get(a, 0.0) > 0]
        if not ages:
            # 這一組完全沒有權重（例如人口為零），只能整組填平均值
            out.update({a: target for a in range(lo, hi + 1)})
            continue

        shape = {a: max(_hermite(xs, ys, ms, a), 0.0) for a in ages}
        out.update(_calibrate(shape, weight, target, cap))
        for a in range(lo, hi + 1):
            out.setdefault(a, target)
    return out


def _calibrate(
    shape: dict[int, float],
    weight: dict[int, float],
    target: float,
    cap: float,
) -> dict[int, float]:
    """組內等比例縮放，讓加權平均命中 target；撞到 cap 的年齡釘住後再分配剩下的。

    這是 IPF（iterative proportional fitting）的一維退化版，
    跟公式 D 解校準係數 k 是同一套想法。
    """
    total_w = sum(weight[a] for a in shape)
    need = target * total_w                  # 這一組要湊出的加權總量
    fixed: dict[int, float] = {}
    free = dict(shape)

    for _ in range(len(shape) + 1):
        denom = sum(weight[a] * v for a, v in free.items())
        if denom <= 0:
            # 形狀整條是零（外推被夾掉），只能在自由項上均攤
            flat = need / sum(weight[a] for a in free) if free else 0.0
            return {**fixed, **{a: min(flat, cap) for a in free}}

        k = need / denom
        over = [a for a, v in free.items() if k * v > cap]
        if not over:
            return {**fixed, **{a: k * v for a, v in free.items()}}

        for a in over:                        # 釘在上限，把剩下的量丟給其他人
            fixed[a] = cap
            need -= weight[a] * cap
            del free[a]
        if not free:
            return fixed

    return {**fixed, **free}
