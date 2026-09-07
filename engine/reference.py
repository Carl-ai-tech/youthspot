"""參考資料層：單一年齡人口 P(a) 與分齡發生率 r(a)。

Spec §6.3 把這兩張表列為「資料層的兩個死穴」：
  1. 單一年齡人口數 —— 沒有它，所有插補公式歸零。
  2. 分齡勞參率     —— 沒有它只能停在 T1，就業類指標有 20%+ 系統性誤差。

載入順序：
  1. data/reference_ntpc.json      官方資料，由 data/build_reference.py 產生
  2. data/reference_placeholder.json  示意數字（來自 Spec §5.3 範例），前者不存在時的後備

換資料不需要動這一層以上的任何程式碼 —— 這正是把兩個死穴隔離在一個檔案裡的目的。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .schema import AgeBand

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLACEHOLDER_FILE = DATA_DIR / "reference_placeholder.json"
OFFICIAL_FILE = DATA_DIR / "reference_ntpc.json"


class ReferenceData:
    """提供 P(a) 與 r(a) 的查表服務。"""

    def __init__(self, payload: dict) -> None:
        self._status = payload.get("_status", "UNKNOWN")
        self.region = payload["region"]
        self.year = payload["year"]
        self.population_source = payload["population"]["source"]
        self.path: Path | None = None
        # 內部一律存 int -> float，避免 JSON 字串鍵造成的查表 bug
        self._population: dict[int, float] = {
            int(a): float(v) for a, v in payload["population"]["by_age"].items()
        }
        self._rates: dict[str, dict[int, float]] = {}
        self._rate_meta: dict[str, dict] = {}
        for key, block in payload.get("rates", {}).items():
            self._rates[key] = {int(a): float(v) for a, v in block["by_age"].items()}
            self._rate_meta[key] = {
                "source": block.get("source", ""),
                "note": block.get("note", ""),
                "official_bands": block.get("official_bands", {}),
            }

    # -- 載入 ---------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ReferenceData":
        """不指定路徑時優先用官方資料，抓不到才退回示意資料。"""
        p = Path(path) if path else (
            OFFICIAL_FILE if OFFICIAL_FILE.exists() else PLACEHOLDER_FILE
        )
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        obj = cls(payload)
        obj.path = p
        return obj

    @classmethod
    def load_placeholder(cls) -> "ReferenceData":
        """明確要示意資料（測試用來比對 Spec §5.3 / §5.4 的手算值）。"""
        return cls.load(PLACEHOLDER_FILE)

    @property
    def is_placeholder(self) -> bool:
        return self._status == "PLACEHOLDER"

    # -- P(a) ---------------------------------------------------------------

    def population(self, age: int) -> float:
        try:
            return self._population[age]
        except KeyError:
            raise KeyError(
                f"缺少 {age} 歲的單一年齡人口數。"
                f"目前涵蓋 {self.age_coverage.label}。（Spec §6.3 死穴 1）"
            ) from None

    def population_sum(self, band: AgeBand) -> float:
        return sum(self.population(a) for a in self._clip(band))

    @property
    def age_coverage(self) -> AgeBand:
        ages = sorted(self._population)
        return AgeBand(ages[0], ages[-1])

    # -- r(a) ---------------------------------------------------------------

    def has_rate(self, key: str) -> bool:
        return key in self._rates

    def rate(self, key: str, age: int) -> float:
        curve = self._rates.get(key)
        if curve is None:
            raise KeyError(f"沒有名為 {key!r} 的分齡發生率曲線")
        if age not in curve:
            raise KeyError(f"曲線 {key!r} 缺少 {age} 歲的值")
        return curve[age]

    def rate_meta(self, key: str) -> dict:
        return self._rate_meta.get(key, {})

    # -- 曲線的「官方組別形狀」-------------------------------------------

    def official_bands(self, key: str) -> list[tuple[AgeBand, float]]:
        """該曲線在官方原始公布時的組別與組值，依年齡排序。"""
        raw = self._rate_meta.get(key, {}).get("official_bands", {})
        out = []
        for label, value in raw.items():
            try:
                out.append((AgeBand.parse(label), float(value)))
            except (ValueError, TypeError):
                continue
        return sorted(out, key=lambda t: t[0].start)

    def shape_reversal(self, key: str, age: int) -> float | None:
        """量測「這一歲所在的官方組別，是不是一個明顯的局部高峰或低谷」。

        回傳相對翻轉幅度：0 代表單調（好插補），數字越大代表組內藏著
        我們看不到的轉折（難插補）。無法判斷時回傳 None。

        為什麼要量這個：backtest.py 的回測顯示，勞參率那種單調上升的曲線
        拆得準（誤差降到不拆的 30%），但失業率在 20-24 歲有個高峰，
        合併後那個峰就消失了，拆出來反而比不拆更差。所以「曲線單不單調」
        才是插補可不可信的真正指標。

        用兩步之中「較小的那一步」當幅度：一路上升後轉平（例如勞參率
        62.75% → 92.71% → 92.23%）不算高峰，那只是進入高原。
        """
        bands = self.official_bands(key)
        if len(bands) < 3:
            return None
        idx = next((i for i, (b, _) in enumerate(bands) if b.covers(AgeBand(age, age))), None)
        if idx is None or idx == 0 or idx == len(bands) - 1:
            return None                      # 端點沒有兩側鄰居，判斷不了
        prev_v, cur_v, next_v = bands[idx - 1][1], bands[idx][1], bands[idx + 1][1]
        up, down = cur_v - prev_v, next_v - cur_v
        if up * down >= 0:
            return 0.0                       # 同向 = 單調
        if cur_v <= 0:
            return None
        return min(abs(up), abs(down)) / cur_v

    def effective_population(self, key: str, band: AgeBand) -> float:
        """有效母體 Σ P(a)·r(a)（公式 B 的分子/分母）。"""
        return sum(
            self.population(a) * self.rate(key, a) for a in self._clip(band)
        )

    # -- 內部 ---------------------------------------------------------------

    def _clip(self, band: AgeBand) -> Iterable[int]:
        """把區間夾到我們實際有資料的範圍內。

        來源資料常出現 "65+" 這種開放區間；夾到 age_coverage 是唯一
        能讓權重算得出來的做法，但這會讓分母被截斷 —— 呼叫端要負責
        在 note 裡標明。
        """
        cov = self.age_coverage
        clipped = band.overlap(cov)
        if clipped is None:
            return ()
        return clipped.ages()

    def clip_note(self, band: AgeBand) -> str:
        """若區間被截斷，回傳一句說明；否則回傳空字串。"""
        cov = self.age_coverage
        if cov.covers(band):
            return ""
        return (
            f"來源分組 {band.label} 超出參考人口涵蓋範圍 {cov.label}，"
            f"分母已截斷至 {cov.label}"
        )
