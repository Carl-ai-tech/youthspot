"""中文問答的測試。全部離線。

測的重點：**問到我們沒有的東西時，會不會老實說沒有。**

一個會硬掰的問答系統比沒有問答更危險 —— 評審隨口問一句，
它給一個看起來很合理的錯答案，沒有人會當場發現，但那就是假的。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.ask import Intent, Vocabulary, ask, resolve  # noqa: E402
from llm.backend import StubBackend  # noqa: E402

PAYLOAD = {
    "meta": {
        "region": "新北市",
        "age_groups": ["18-24", "25-29", "30-35", "18-35"],
        "metrics": ["人口數", "平均年薪", "職缺數"],
        "educations": ["大專及以上"],
        "industries": ["醫療保健及社會工作服務業"],
    },
    "records": [
        {"region": "新北市", "age_group": "25-29", "metric": "平均年薪",
         "value": 59.9, "unit": "萬元/年", "education": None,
         "provenance": {"confidence": "high", "note": "分組完全吻合"}},
        {"region": "新北市", "age_group": "18-35", "metric": "人口數",
         "value": 832214, "unit": "人", "education": None,
         "provenance": {"confidence": "high", "note": "單一年齡直接加總"}},
        {"region": "新北市板橋區", "age_group": "18-35", "metric": "人口數",
         "value": 106473, "unit": "人", "education": None,
         "provenance": {"confidence": "high", "note": ""}},
        {"region": "新北市平溪區", "age_group": "18-35", "metric": "人口數",
         "value": 546, "unit": "人", "education": None,
         "provenance": {"confidence": "high", "note": ""}},
    ],
}


def stub(**intent) -> StubBackend:
    base = {"kind": "lookup", "region": None, "age_group": None,
            "metric": None, "education": None, "industry": None, "reason": ""}
    base.update(intent)
    return StubBackend({"__default__": json.dumps(base, ensure_ascii=False)})


class TestVocabulary(unittest.TestCase):
    def test_built_from_the_data_not_hand_written(self) -> None:
        """能問的東西必須從資料長出來，手寫清單一定會跟資料脫節。"""
        v = Vocabulary.from_payload(PAYLOAD)
        self.assertIn("新北市板橋區", v.regions)
        self.assertIn("平均年薪", v.metrics)
        self.assertEqual(len(v.regions), 3)


class TestControlledChoice(unittest.TestCase):
    """模型只能從清單裡挑，挑清單外的東西一律當沒挑。"""

    def test_invented_metric_is_discarded(self) -> None:
        a = ask("新北市的幸福指數多少", PAYLOAD, stub(metric="幸福指數", region="新北市"))
        self.assertFalse(a.answered)
        self.assertIsNone(a.intent.metric)

    def test_partial_region_name_still_matches(self) -> None:
        """使用者說「板橋區」，資料裡是「新北市板橋區」。"""
        a = ask("板橋區有多少青年", PAYLOAD, stub(region="板橋區", metric="人口數",
                                              age_group="18-35"))
        self.assertTrue(a.answered)
        self.assertIn("106,473", a.text)


class TestHonestRefusal(unittest.TestCase):
    """這一組是整支程式最重要的部分。"""

    def test_says_no_when_the_model_says_unanswerable(self) -> None:
        b = stub(kind="unanswerable", reason="我們沒有居住相關的資料")
        a = ask("新北市青年的居住情況如何", PAYLOAD, b)
        self.assertFalse(a.answered)
        self.assertIn("居住", a.text)

    def test_says_no_when_the_combination_has_no_data(self) -> None:
        """指標存在、地區存在，但這個組合沒資料 —— 不能拿別的來充數。"""
        a = resolve(Intent(kind="lookup", region="新北市平溪區",
                           age_group="25-29", metric="平均年薪"),
                    PAYLOAD["records"], "新北市")
        self.assertFalse(a.answered)
        self.assertEqual(a.records, [])

    def test_says_no_without_a_metric(self) -> None:
        a = ask("新北市怎麼樣", PAYLOAD, stub(region="新北市"))
        self.assertFalse(a.answered)

    def test_unparseable_model_output_is_not_a_crash(self) -> None:
        a = ask("隨便問", PAYLOAD, StubBackend({"__default__": "我不知道欸"}))
        self.assertFalse(a.answered)


class TestAnswers(unittest.TestCase):
    def test_lookup_cites_the_record(self) -> None:
        a = ask("新北市 25-29 歲平均年薪", PAYLOAD,
                stub(region="新北市", age_group="25-29", metric="平均年薪"))
        self.assertTrue(a.answered)
        self.assertIn("59.9", a.text)
        self.assertEqual(len(a.records), 1)
        self.assertIn("provenance", a.records[0])   # 答案一定附得出履歷

    def test_high_confidence_is_stated(self) -> None:
        a = ask("新北市 25-29 歲平均年薪", PAYLOAD,
                stub(region="新北市", age_group="25-29", metric="平均年薪"))
        self.assertIn("沒有經過推估", a.text)

    def test_rank_orders_and_excludes_the_whole_city(self) -> None:
        """問「哪一區最多」時，全市合計不該混在行政區排名裡。"""
        a = ask("哪一區的青年最多", PAYLOAD,
                stub(kind="rank", metric="人口數", age_group="18-35"))
        self.assertTrue(a.answered)
        self.assertIn("板橋", a.text)
        self.assertNotIn("新北市　", a.text)
        self.assertEqual(a.records[0]["region"], "新北市板橋區")

    def test_answer_never_contains_a_number_the_model_produced(self) -> None:
        """模型回的 JSON 裡就算塞了數字，也絕對不會出現在答案裡。"""
        b = StubBackend({"__default__": json.dumps(
            {"kind": "lookup", "region": "新北市", "age_group": "25-29",
             "metric": "平均年薪", "value": 999.9, "answer": "是 999.9 萬"},
            ensure_ascii=False)})
        a = ask("新北市 25-29 歲平均年薪", PAYLOAD, b)
        self.assertIn("59.9", a.text)
        self.assertNotIn("999", a.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
