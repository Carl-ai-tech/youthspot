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

    def test_used_evidence_is_not_a_gap_because_later_sentence_has_gap(self):
        text = "各區租金中位數已用來比較。通勤與青年淨遷入訊號也已參考。缺少住宅供給資料。"
        self.assertEqual([g["key"] for g in find_gaps(text)], ["住宅供給（建照／使照）"])

    def test_gap_heading_bullets_inherit_but_evidence_section_does_not(self):
        text = "## 資料缺口\n- 青年遷入與遷出流量\n- 住宅供給\n## 證據驗證\n- 各區租金中位數\n- 女性人口"
        self.assertEqual({g["key"] for g in find_gaps(text)}, {"青年流出率／人口外流", "住宅供給（建照／使照）"})

    def test_normal_paragraph_ends_gap_list(self):
        text = "待補充資料：\n1. 通勤流向\n目前已用各區租金比較。\n- 女性人口"
        self.assertEqual([g["key"] for g in find_gaps(text)], ["通勤流向"])

    def test_positive_bullet_is_not_a_gap(self):
        text = "資料缺口：\n- 已有各區租金中位數\n- 住宅供給"
        self.assertEqual([g["key"] for g in find_gaps(text)], ["住宅供給（建照／使照）"])

    def test_rental_topic_does_not_imply_subsidy_gap(self):
        self.assertEqual(find_gaps("缺少青年租屋意願調查。"), [])

    def test_plain_status_distinguishes_proxy_and_candidate(self):
        got = {g["key"]: g for g in find_gaps("缺少青年遷入流量、行政區薪資、通勤流向、住宅供給、租金補貼。")}
        for key in ("青年流出率／人口外流", "行政區的薪資", "通勤流向"):
            self.assertEqual(got[key]["display_status"], "proxy")
        for key in ("住宅供給（建照／使照）", "青年租金補貼／社會住宅"):
            self.assertEqual(got[key]["display_status"], "candidate")
            self.assertIn("待確認", got[key]["plain_note"])
        self.assertEqual(got["青年流出率／人口外流"]["status"], "external")

    def test_available_data_claim_in_gap_is_explained(self):
        got = find_gaps("缺少各區租金中位數。")
        self.assertEqual(got[0]["display_status"], "available")
        self.assertIn("登錄案件", got[0]["plain_note"])

    def test_negated_gap_is_not_reported(self):
        self.assertEqual(find_gaps("沒有缺少各區租金資料。"), [])

    def test_contrasting_clauses_keep_only_missing_topic(self):
        text = "已用各區租金中位數，但缺少住宅供給資料。"
        self.assertEqual([g["key"] for g in find_gaps(text)], ["住宅供給（建照／使照）"])

    def test_every_catalog_entry_has_a_real_note(self):
        for item in CATALOG:
            self.assertIn(item["status"], ("missing", "available", "external"))
            self.assertGreater(len(item["note"]), 20, item["key"])
            self.assertIn(item["display_status"], ("available", "proxy", "needed", "candidate"))
            self.assertGreater(len(item["plain_note"]), 15)
            self.assertNotIn("有但未接入", item["plain_note"])


if __name__ == "__main__":
    unittest.main()
