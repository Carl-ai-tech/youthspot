"""世代淨遷徙（data/fetch_migration.py）。"""

from __future__ import annotations

import unittest

from data import fetch_migration as fm


class TestMerge(unittest.TestCase):
    def test_split_offices_are_merged(self):
        d = {"高雄市三民一": {18: 10, 19: 5}, "高雄市三民二": {18: 3, 19: 7}, "高雄市鳳山區": {18: 1, 19: 1}}
        out = fm._merge_split_districts(d, "高雄市")
        self.assertEqual(set(out), {"高雄市三民區", "高雄市鳳山區"})
        self.assertEqual(out["高雄市三民區"], {18: 13, 19: 12})


class TestRealCache(unittest.TestCase):
    def test_cohort_beats_population_difference(self):
        from data.sources import CACHE_DIR
        if not (CACHE_DIR / "odrp014_10707_p1.json").exists():
            self.skipTest("快取不在")
        d = fm.fetch_migration("新北市", period="11507")
        self.assertEqual(d["years"][0], 2018)   # 106/7 起用 ODRP005，序列從 2018 開始
        tw = d["areas"]["新北市淡水區"]
        # 淡水：人口差負、世代淨遷入正 —— 這就是整個指標存在的理由
        self.assertLess(tw["naive"][-1], 0)
        self.assertGreater(tw["net"][-1], 0)
        # 各區加總 = 全市（fetch 內部已驗，這裡再守一次）
        parts = sum(a["net"][-1] for k, a in d["areas"].items() if k != "新北市")
        self.assertEqual(parts, d["areas"]["新北市"]["net"][-1])


if __name__ == "__main__":
    unittest.main()
