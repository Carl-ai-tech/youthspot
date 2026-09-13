"""跨城市問答：點名別的城市（沒點名區）也要拿到它各區的淨遷入與逐年序列，問到某一年要有該年的排名。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy.lambda_handler import _load_unified, _merge_mentioned  # noqa: E402
from llm.advise import _cross_district_records, _migration_lines, _mentioned_records  # noqa: E402

Q = "桃園流入最高的區域和新北流入最高的區域 2025 做對比"


class TestCrossCity(unittest.TestCase):
    def setUp(self):
        self.payload = _merge_mentioned(_load_unified("新北市"), Q, "新北市")

    def test_named_city_without_district_brings_its_district_records(self):
        self.assertIn("桃園市", self.payload.get("_cross_city") or [])
        self.assertIn("桃園市", self.payload.get("_cross_migration") or {})
        cross = _cross_district_records(self.payload, Q, "新北市", [])
        regions = {r["region"] for r in cross}
        self.assertTrue(any(r.startswith("桃園市") and r != "桃園市" for r in regions))
        self.assertTrue(any(r["region"].startswith("桃園市") and r["metric"] == "青年淨遷入率" for r in cross))
        # 「桃園」兩個字也算點名桃園市：市層級記錄要排到前面
        men = _mentioned_records(self.payload, Q, [])
        self.assertTrue(any(r["region"] == "桃園市" for r in men))

    def test_year_in_question_gets_that_years_ranking(self):
        lines = "\n".join(_migration_lines(self.payload, "新北市", Q))
        self.assertIn("桃園市各區 18–35 歲世代淨遷入率逐年", lines)
        self.assertIn("★ 2025 年桃園市淨遷入率最高", lines)
        self.assertIn("★ 2025 年新北市淨遷入率最高：淡水區", lines)
        self.assertIn("2025 年 +5.3%（+2,154 人）", lines)          # 淡水 2025 的值要在查核池裡

    def test_home_only_question_has_no_cross_block(self):
        p = _merge_mentioned(_load_unified("新北市"), "淡水區的青年在增加嗎", "新北市")
        self.assertNotIn("_cross_city", p)
        self.assertEqual(_cross_district_records(p, "淡水區的青年在增加嗎", "新北市", []), [])


if __name__ == "__main__":
    unittest.main()
