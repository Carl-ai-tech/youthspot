"""產生 reference_placeholder.json（示意參考資料）。

⚠️ 這裡的數字是**示意值**，不是真實統計。
   15–24 歲人口與分齡勞參率刻意採用 Spec §5.3 / §5.4 的範例數字，
   好讓 demo 能重現文件裡的 72,470 vs 96,700 對比。

賽前請用真實資料取代 reference_placeholder.json：
  - 人口   → 內政部戶政司 / 新北市統計資料庫「單一年齡人口數」
  - 勞參率 → 主計總處「人力資源調查」分齡勞動力參與率
取代後本檔案即可刪除。

執行：python data/_make_placeholder.py
"""

import json
from pathlib import Path


def ramp(spec: dict[range, tuple[float, float]]) -> dict[int, float]:
    """把 {年齡區間: (起值, 迄值)} 展開成逐齡線性內插的曲線。"""
    out: dict[int, float] = {}
    for rng, (lo, hi) in spec.items():
        ages = list(rng)
        span = max(len(ages) - 1, 1)
        for i, a in enumerate(ages):
            out[a] = round(lo + (hi - lo) * i / span, 4)
    return out


# --- 單一年齡人口 P(a)，單位：千人 ------------------------------------------
# 15–24 直接採用 Spec §5.3 的範例（38, 39, ... 47），其餘為合理形狀的示意值。
population = {a: float(38 + (a - 15)) for a in range(15, 25)}       # 38 → 47
population |= ramp({range(25, 30): (48, 54)})                       # 25–29
population |= ramp({range(30, 36): (56, 59)})                       # 30–35
population |= ramp({range(36, 45): (58, 54)})                       # 36–44
population |= ramp({range(45, 65): (54, 43)})                       # 45–64

# --- 分齡勞動參與率 r(a) ----------------------------------------------------
# 15–24 採用 Spec §5.4 的範例：15–17 = 5%、18–21 = 40%、22–24 = 75%
lfpr = {a: 0.05 for a in range(15, 18)}
lfpr |= {a: 0.40 for a in range(18, 22)}
lfpr |= {a: 0.75 for a in range(22, 25)}
lfpr |= ramp({range(25, 30): (0.92, 0.92)})
lfpr |= ramp({range(30, 36): (0.91, 0.86)})
lfpr |= ramp({range(36, 45): (0.85, 0.83)})
lfpr |= ramp({range(45, 65): (0.82, 0.45)})

# --- 全國分齡失業率曲線 u_nat(a) --------------------------------------------
# 公式 D 只用它的「形狀」，絕對水準會在校準步驟被 k 吸收掉。
# 形狀依 Spec §5.5：18–21 歲失業率約為 22–24 歲的 1.4 倍。
unemployment = ramp({range(15, 18): (0.090, 0.090)})
unemployment |= ramp({range(18, 22): (0.119, 0.119)})   # = 0.085 × 1.4
unemployment |= ramp({range(22, 25): (0.085, 0.085)})
unemployment |= ramp({range(25, 30): (0.060, 0.045)})
unemployment |= ramp({range(30, 36): (0.038, 0.030)})
unemployment |= ramp({range(36, 65): (0.028, 0.022)})

payload = {
    "_status": "PLACEHOLDER",
    "_warning": "示意數字，非真實統計。賽前必須以官方資料取代（Spec §6.3）。",
    "region": "新北市",
    "year": 2025,
    "population": {
        "source": "示意值（形狀取自 Spec §5.3 範例）",
        "unit": "千人",
        "by_age": {str(a): v for a, v in sorted(population.items())},
    },
    "rates": {
        "labor_force_participation": {
            "source": "示意值（15–24 取自 Spec §5.4 範例）",
            "note": "地方層級若拿不到，用全國分齡勞參率代替並註明（Spec §5.4）",
            "by_age": {str(a): v for a, v in sorted(lfpr.items())},
        },
        "unemployment_national": {
            "source": "示意值（形狀依 Spec §5.5：18–21 約為 22–24 的 1.4 倍）",
            "note": "公式 D 只取形狀，水準由 benchmark 校準決定",
            "by_age": {str(a): v for a, v in sorted(unemployment.items())},
        },
    },
}

out = Path(__file__).with_name("reference_placeholder.json")
out.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"已寫入 {out}（人口 {len(population)} 個年齡、曲線 {len(payload['rates'])} 條）")
