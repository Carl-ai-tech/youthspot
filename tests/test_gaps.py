"""模型說「缺什麼資料」→ 系統對照資料目錄（llm/gaps.py）。"""

from __future__ import annotations

import unittest

from llm.gaps import CATALOG, find_gaps


class TestGaps(unittest.TestCase):

    def test_typical_suggestion_is_mapped(self):
        t = "建議補蒐集：新北市各主要產業的青年就業人數、該行業的招聘困難度、青年流出率。"
        got = {g["key"]: g["status"] for g in find_gaps(t)}
        self.assertEqual(got.get("行業 × 年齡的就業人數"), "missing")
        self.assertEqual(got.get("行業招聘困難度／缺工程度"), "available")
        self.assertEqual(got.get("青年流出率／人口外流"), "external")

    def test_no_gap_talk_no_output(self):
        self.assertEqual(find_gaps("林口區 18-35 歲青年 27,415 人，勞參率 80.2%。"), [])

    def test_district_unemployment_is_missing_with_substitute(self):
        got = find_gaps("資料限制：缺少各區的失業率與就業人數。")
        keys = {g["key"] for g in got}
        self.assertIn("行政區的失業率／就業人數", keys)
        self.assertIn("工作機會密度", next(g["note"] for g in got if g["key"] == "行政區的失業率／就業人數"))

    def test_every_catalog_entry_has_a_real_note(self):
        for item in CATALOG:
            self.assertIn(item["status"], ("missing", "available", "external"))
            self.assertGreater(len(item["note"]), 20, item["key"])


if __name__ == "__main__":
    unittest.main()
