"""RAG 統整的測試。全部離線。

測的重點只有一個：**模型編出來的數字，抓不抓得到。**

RAG 最大的問題不是模型不會寫，是它會寫出一段很流暢、
但裡面有一兩個數字是編的文字 —— 而讀的人完全看不出來是哪一兩個。
所以這裡的測試幾乎都在測「驗證那一步」。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.backend import StubBackend  # noqa: E402
from llm.synthesize import build_prompt, retrieve, synthesize, verify  # noqa: E402

PAYLOAD = {
    "records": [
        {"region": "新北市", "age_group": "18-35", "metric": "人口數",
         "value": 832214.0, "unit": "人", "education": None,
         "provenance": {"confidence": "high"}},
        {"region": "新北市", "age_group": "18-35", "metric": "勞動力參與率",
         "value": 0.8098, "unit": "%", "education": None,
         "provenance": {"confidence": "medium"}},
        {"region": "新北市", "age_group": "25-29", "metric": "平均年薪",
         "value": 59.9, "unit": "萬元/年", "education": None,
         "provenance": {"confidence": "high"}},
        {"region": "新北市", "age_group": "18-24", "metric": "失業率",
         "value": 0.103, "unit": "%", "education": None,
         "provenance": {"confidence": "low"}},
        {"region": "新北市", "age_group": "15-64", "metric": "平均年薪",
         "value": 75.3, "unit": "萬元/年", "education": "大專及以上",
         "provenance": {"confidence": "medium"}},
    ],
    "benchmark": {
        "band": "25-29", "cities": ["臺北市", "新北市"],
        "metrics": {
            "平均年薪": {"values": {"臺北市": 69.7, "新北市": 59.9},
                       "rank": {"臺北市": 1, "新北市": 4}, "unit": "萬元/年"},
        },
    },
    "trends": {"salary": {"years": [2019, 2024], "mean": {"25-29": [51.8, 59.9]}}},
}


class TestRetrieval(unittest.TestCase):
    def test_pulls_records_for_the_asked_scope(self) -> None:
        got = retrieve(PAYLOAD, "新北市", "18-35")
        self.assertTrue(got)
        self.assertTrue(any(r["metric"] == "人口數" for r in got))

    def test_no_duplicates(self) -> None:
        got = retrieve(PAYLOAD, "新北市", "18-35")
        keys = [(r["region"], r["age_group"], r["metric"], r.get("education")) for r in got]
        self.assertEqual(len(keys), len(set(keys)))


class TestPrompt(unittest.TestCase):
    def test_lists_the_only_allowed_numbers(self) -> None:
        p = build_prompt(PAYLOAD, retrieve(PAYLOAD, "新北市", "18-35"), "新北市", "18-35")
        self.assertIn("你只能使用下面列出的數字", p)
        self.assertIn("832,214", p)

    def test_carries_confidence_into_the_prompt(self) -> None:
        """模型要知道哪些數字比較不可靠，才不會拿 low 的數字下強結論。"""
        p = build_prompt(PAYLOAD, retrieve(PAYLOAD, "新北市", "18-35"), "新北市", "18-35")
        self.assertIn("可靠度 low", p)


class TestVerification(unittest.TestCase):
    """這一組是重點。"""

    def test_accepts_numbers_that_came_from_the_records(self) -> None:
        text = "新北市 18-35 歲有 832,214 名青年，25-29 歲平均年薪 59.9 萬元。"
        ok, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertEqual(bad, [])
        self.assertIn("832,214", ok)

    def test_catches_an_invented_number(self) -> None:
        """最重要的一條：模型編一個沒出現過的數字，必須被抓出來。"""
        text = "新北市青年平均月薪為 4.8 萬元，居六都之冠。"
        ok, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertIn("4.8", bad)

    def test_catches_a_plausible_but_wrong_number(self) -> None:
        """把 832,214 寫成 832,000 —— 看起來很像，但不是我們算出來的那個。"""
        text = "新北市有 923,500 名青年。"
        _, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertIn("923,500", bad)

    def test_percentages_are_matched_after_scaling(self) -> None:
        """記錄裡存 0.103，敘述裡寫 10.3% —— 這是同一個數字，不該報錯。"""
        text = "18-24 歲失業率 10.3%。"
        _, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertEqual(bad, [])

    def test_years_and_ages_are_not_treated_as_data(self) -> None:
        text = "2024 年的 25-29 歲青年，在六都排第 4 名。"
        _, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertEqual(bad, [])

    def test_benchmark_values_count_as_grounded(self) -> None:
        text = "臺北市同齡平均年薪 69.7 萬，高於新北市。"
        _, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertEqual(bad, [])

    def test_trend_values_count_as_grounded(self) -> None:
        text = "25-29 歲年薪從 51.8 萬升到 59.9 萬。"
        _, bad = verify(text, PAYLOAD["records"], PAYLOAD)
        self.assertEqual(bad, [])


class TestEndToEnd(unittest.TestCase):
    def test_clean_output_is_marked_trustworthy(self) -> None:
        good = "新北市 18-35 歲共 832,214 名青年，25-29 歲平均年薪 59.9 萬元，六都排第 4。"
        g = synthesize(PAYLOAD, StubBackend({"__default__": good}))
        self.assertTrue(g.trustworthy)
        self.assertIn("全部比對到來源記錄", g.summary())

    def test_hallucinated_output_is_flagged_not_hidden(self) -> None:
        """編造的敘述不能被丟掉也不能被默默接受 —— 要標出來讓人判斷。"""
        bad = "新北市青年平均月薪 4.8 萬元，租金負擔率高達 38.2%。"
        g = synthesize(PAYLOAD, StubBackend({"__default__": bad}))
        self.assertFalse(g.trustworthy)
        self.assertGreaterEqual(len(g.unverified), 2)
        self.assertIn("對不上", g.summary())

    def test_records_are_returned_for_citation(self) -> None:
        g = synthesize(PAYLOAD, StubBackend({"__default__": "新北市有 832,214 名青年。"}))
        self.assertTrue(g.records)
        self.assertIn("provenance", g.records[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
