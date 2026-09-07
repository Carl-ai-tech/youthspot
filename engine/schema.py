"""統一資料 Schema 與型別定義（對應 Spec §7.3）。

這個檔案是 A（資料/AI）與 B（前端）的協作介面，第一天敲定後就寫死。
前端只要照 AlignedRecord.to_dict() 的輸出開發即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 目標口徑：《青年基本法》18–35 歲，細分三組（Spec §5.1）
# ---------------------------------------------------------------------------

class AgeBand:
    """年齡區間，閉區間 [start, end]。"""

    __slots__ = ("start", "end")

    def __init__(self, start: int, end: int) -> None:
        if start > end:
            raise ValueError(f"年齡區間顛倒：{start}–{end}")
        self.start = start
        self.end = end

    @classmethod
    def parse(cls, label: str) -> "AgeBand":
        """解析常見的年齡分組寫法。

        支援 "18-24"、"18–24"（en dash）、"65+"、"未滿 25"、"15歲以上"。
        解析不了就丟 ValueError —— 交給 §5.6 的 LLM 語意判讀處理。
        """
        s = label.strip().replace("–", "-").replace("—", "-").replace(" ", "")
        s = s.replace("歲", "")

        if s.endswith("+") or s.endswith("以上"):
            head = s.rstrip("+").removesuffix("以上")
            return cls(int(head), OPEN_ENDED_TOP)
        if s.startswith("未滿"):
            return cls(0, int(s.removeprefix("未滿")) - 1)
        if s.endswith("以下"):
            return cls(0, int(s.removesuffix("以下")))
        if "-" in s:
            lo, hi = s.split("-", 1)
            return cls(int(lo), int(hi))
        raise ValueError(f"無法解析年齡分組：{label!r}")

    @property
    def label(self) -> str:
        if self.end >= OPEN_ENDED_TOP:
            return f"{self.start}+"
        return f"{self.start}-{self.end}"

    def ages(self) -> range:
        return range(self.start, self.end + 1)

    def overlap(self, other: "AgeBand") -> "AgeBand | None":
        """回傳兩個區間的交集，無交集回傳 None。"""
        lo = max(self.start, other.start)
        hi = min(self.end, other.end)
        return AgeBand(lo, hi) if lo <= hi else None

    def covers(self, other: "AgeBand") -> bool:
        return self.start <= other.start and self.end >= other.end

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, AgeBand)
            and self.start == other.start
            and self.end == other.end
        )

    def __hash__(self) -> int:
        return hash((self.start, self.end))

    def __repr__(self) -> str:
        return f"AgeBand({self.label})"


# 開放區間（如 "65+"）的上界。設 120 讓它可以參與數學運算而不特例化。
OPEN_ENDED_TOP = 120

# 目標標準分組（Spec §5.1）
TARGET_BANDS: tuple[AgeBand, ...] = (
    AgeBand(18, 24),
    AgeBand(25, 29),
    AgeBand(30, 35),
)

# 《青年基本法》全區間
YOUTH_BAND = AgeBand(18, 35)


# ---------------------------------------------------------------------------
# 指標分類（Spec §5.2）—— 整個引擎最容易做錯的地方
# ---------------------------------------------------------------------------

class MetricKind(Enum):
    """加總型 vs 比率型。兩者的插補方式完全不同。"""

    EXTENSIVE = "extensive"   # 可相加的絕對數量：就業人數、畢業生數
    INTENSIVE = "intensive"   # 由兩個加總型相除：失業率、平均薪資


class Method(str, Enum):
    """provenance.method 的合法值（Spec §7.3）。"""

    EXACT_MATCH = "exact_match"
    FORMULA_A_T1 = "formula_a_t1"
    FORMULA_B_T2 = "formula_b_t2"
    FORMULA_C = "formula_c"
    FORMULA_D_T3 = "formula_d_t3"
    LLM_INFERRED = "llm_inferred"


class Confidence(str, Enum):
    """provenance.confidence 的合法值（Spec §7.3 / §5.8）。"""

    HIGH = "high"          # 邊界完全吻合          → 實線
    MEDIUM = "medium"      # T2 / T3 插補          → 虛線 + ⓘ
    LOW = "low"            # T1 插補、或跨結構轉折  → 虛線 + ⚠️ + 推估區間
    INFERRED = "inferred"  # LLM 語意判定          → 灰色 + 「AI 推定」


@dataclass(slots=True)
class Provenance:
    """每個數值的來源與轉換履歷。P0-5 要求每張圖都能點開看到這些。"""

    source_agency: str
    source_dataset: str
    source_age_group: str
    method: Method
    weight: float
    confidence: Confidence
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_agency": self.source_agency,
            "source_dataset": self.source_dataset,
            "source_age_group": self.source_age_group,
            "method": self.method.value,
            "weight": round(self.weight, 6),
            "confidence": self.confidence.value,
            "note": self.note,
        }


@dataclass(slots=True)
class AlignedRecord:
    """unified.json 的一列。前端消費的就是這個結構。"""

    region: str
    year: int
    age_group: str
    gender: str
    metric: str
    value: float
    unit: str
    provenance: Provenance
    education: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "region": self.region,
            "year": self.year,
            "age_group": self.age_group,
            "gender": self.gender,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
        }
        if self.education is not None:
            d["education"] = self.education
        d.update(self.extras)
        d["provenance"] = self.provenance.to_dict()
        return d


@dataclass(slots=True)
class SourceRecord:
    """從政府資料源清理後、尚未對齊的一列。"""

    region: str
    year: int
    age_band: AgeBand
    metric: str
    value: float
    unit: str
    kind: MetricKind
    source_agency: str
    source_dataset: str
    gender: str = "total"
    education: str | None = None
    # 比率型專用：分子/分母的原始數量。有的話才能走公式 C/D。
    numerator: float | None = None
    denominator: float | None = None
    # 這個指標的「有效母體發生率」用哪一條曲線（供公式 B 查表）
    rate_key: str | None = None
