"""趨勢外推。—— 命題的預期成果「預測哪些領域缺工」

刻意**不用機器學習**。理由寫在 Spec §10：年度資料點只有十幾個，
訓練出來的模型看起來厲害但沒有可信度，而且無法解釋。
黑客松評審問「你這個模型怎麼來的」，答不出來比沒做還糟。

改用最小平方線性外推，並且**一定附上預測區間**。
「2027 年約 9.5 萬個職缺」沒有用；
「2027 年 9.5 萬個職缺，區間 7.7–11.3 萬」才有用 ——
區間本身就是誠實度，讀的人自己知道能相信到什麼程度。

純函式，不碰 I/O，可單獨測試。
"""

from __future__ import annotations

from math import sqrt

# 95% 常態近似。樣本數少時 t 分布會更寬，這裡選擇不誇大精確度：
# 用 1.96 是低估區間的，所以下面另外要求至少 6 個點才給 medium。
Z95 = 1.96
MIN_POINTS = 6


class Trend:
    """一條線性趨勢，以及它能不能被相信。"""

    __slots__ = ("slope", "intercept", "r2", "se", "n", "mean_x", "sxx", "last_x", "last_y")

    def __init__(self, slope, intercept, r2, se, n, mean_x, sxx, last_x, last_y):
        self.slope, self.intercept, self.r2, self.se = slope, intercept, r2, se
        self.n, self.mean_x, self.sxx = n, mean_x, sxx
        self.last_x, self.last_y = last_x, last_y

    def predict(self, x: float) -> tuple[float, float]:
        """回傳 (預測值, 95% 區間半寬)。外推越遠區間越寬，這是它應有的行為。"""
        yhat = self.intercept + self.slope * x
        margin = Z95 * self.se * sqrt(1 + 1 / self.n + (x - self.mean_x) ** 2 / self.sxx)
        return yhat, margin

    @property
    def annual_change_pct(self) -> float:
        """相對於最新值的年變化率。"""
        return self.slope / self.last_y if self.last_y else 0.0

    @property
    def slope_se(self) -> float:
        """斜率本身的標準誤。"""
        return self.se / sqrt(self.sxx) if self.sxx > 0 else float("inf")

    @property
    def t_stat(self) -> float:
        """殘差為零時（完美直線）標準誤是 0 —— 那是最顯著的情況，不是最不顯著。

        直接寫 `slope / slope_se if slope_se else 0` 會把它判成不顯著，
        剛好顛倒。真實資料不會有這種情形，但邊界條件不能靠運氣。
        """
        if self.slope_se == 0:
            return float("inf") if self.slope != 0 else 0.0
        return self.slope / self.slope_se

    @property
    def significant(self) -> bool:
        """斜率是否顯著不為零（雙尾 5%）。

        這才是「這個行業有沒有在成長」的正確檢定。
        用「三年後的預測區間有沒有跨過現值」去判斷會太保守 ——
        點預測的區間包含了每一年的隨機波動，但我們問的是趨勢本身。
        """
        return abs(self.t_stat) >= _t_critical(self.n - 2)

    def direction(self) -> str:
        if not self.significant:
            return "flat"
        return "up" if self.slope > 0 else "down"

    @property
    def confidence(self) -> str:
        """外推永遠不該宣稱 high。趨勢顯著且點數足夠才給 medium。"""
        if self.n < MIN_POINTS or not self.significant:
            return "low"
        return "medium"


# 雙尾 5% 的 t 臨界值。樣本這麼小，用常態的 1.96 會高估顯著性。
_T_TABLE = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
            7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131,
            20: 2.086, 30: 2.042}


def _t_critical(df: int) -> float:
    if df <= 0:
        return float("inf")
    for k in sorted(_T_TABLE):
        if df <= k:
            return _T_TABLE[k]
    return 1.96


def fit(points: list[tuple[float, float]]) -> Trend | None:
    """最小平方直線。點數不足或 x 全部相同時回傳 None。"""
    n = len(points)
    if n < 3:
        return None
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    if sxx <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x

    resid = [y - (intercept + slope * x) for x, y in points]
    sse = sum(r * r for r in resid)
    sst = sum((y - mean_y) ** 2 for _, y in points)
    se = sqrt(sse / (n - 2)) if n > 2 else 0.0
    r2 = 1 - sse / sst if sst > 0 else 0.0

    last = max(points, key=lambda p: p[0])
    return Trend(slope, intercept, r2, se, n, mean_x, sxx, last[0], last[1])


def annual_means(series: dict[str, float]) -> dict[int, float]:
    """把「年月別 → 值」壓成「西元年 → 該年平均」。

    職缺調查一年跑四次，而且 2024 年以前是 2/5/8/11 月、2025 年起改成 3/6/9/12 月。
    直接拿月資料做迴歸會被季節性和調查月份異動干擾，先取年平均把它洗掉。
    """
    buckets: dict[int, list[float]] = {}
    for period, value in series.items():
        if len(period) < 5 or value is None:
            continue
        try:
            year = int(period[:4])
        except ValueError:
            continue
        buckets.setdefault(year, []).append(value)
    return {y: sum(v) / len(v) for y, v in sorted(buckets.items())}
