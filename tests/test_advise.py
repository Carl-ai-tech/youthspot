"""政策問答與數字查核的測試。

查核機制的價值全在**兩個方向都要準**：
    漏報 → 編造的數字流到使用者面前
    誤報 → 把正確引用標成編造，之後真的抓到時沒有人會信

所以下面一半的測試在檢查「不該報的有沒有閉嘴」。
每一條誤報案例都是實際跑真模型時遇到的。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from llm.advise import _district_records, advise, build_prompt
from llm.backend import StubBackend
from llm.synthesize import _numbers_in

PAYLOAD = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "unified.json")
    .read_text(encoding="utf-8")
)


class TestNumberExtraction(unittest.TestCase):
    """哪些數字算「資料」，哪些不算。全部來自真模型的實際輸出。"""

    def test_real_data_numbers_are_extracted(self):
        self.assertEqual(_numbers_in("板橋 85,457人、新莊 72,035人"),
                         ["85,457", "72,035"])

    def test_list_markers_are_not_data(self):
        """模型用編號清單回答時，每個編號都會被報成編造 —— 實際踩過。"""
        self.assertEqual(_numbers_in("1. 第一點\n2. 第二點\n3. 第三點"), [])

    def test_bold_and_ideographic_list_markers_too(self):
        self.assertEqual(_numbers_in("**2. 加粗編號**\n3、頓號編號"), [])

    def test_citation_indices_are_not_data(self):
        """advise() 的提示詞用 [1] [2] 標記錄，模型會照著引用。"""
        self.assertEqual(_numbers_in("依據 [8] 與 [12] 的記錄"), [])

    def test_years_ranks_and_percentage_points_still_skipped(self):
        """原有的四條跳過規則不能被新規則弄壞。"""
        self.assertEqual(_numbers_in("2024 年為 59.9 萬，排第 4 名"), ["59.9"])
        self.assertEqual(_numbers_in("相差 7.4 個百分點"), [])
        self.assertEqual(_numbers_in("18-35 歲共 832,214 人"), ["832,214"])


class TestDistrictRecords(unittest.TestCase):
    """政策問題多半跟「資源怎麼分到各區」有關，分區資料一定要餵進去。"""

    def test_district_records_are_found(self):
        recs = _district_records(PAYLOAD, "新北市", "18-35")
        self.assertGreater(len(recs), 50, "29 個區 × 三個指標應該有近百筆")
        self.assertTrue(all(r["region"] != "新北市" for r in recs))
        # 分齡的三個指標帶 18-35；行政區層級的全體指標（所得、在地工作機會）帶「全體」
        self.assertTrue(all(r["age_group"] in ("18-35", "全體") for r in recs))
        self.assertTrue(any(r["age_group"] == "18-35" for r in recs))

    def test_sorted_by_size(self):
        recs = _district_records(PAYLOAD, "新北市", "18-35")
        values = [r["value"] for r in recs]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_only_metrics_that_exist_at_district_level(self):
        """薪資、失業率沒有行政區層級，撈進來只會是空的。
        有行政區層級的：分齡三個 ＋ 全體層級的所得（財政部）與在地工作機會（普查）。"""
        metrics = {r["metric"] for r in _district_records(PAYLOAD, "新北市", "18-35")}
        allowed = {"人口數", "勞動力人數", "勞動力參與率", "青年人口年變化率", "青年淨遷入率", "青年淨遷入人數", "綜合所得中位數", "工作機會密度", "在地工作機會", "住宅每坪月租中位數", "住宅月租金中位數", "青年女性一般生育率"}
        self.assertTrue(metrics <= allowed, metrics)
        self.assertNotIn("平均年薪", metrics)
        self.assertNotIn("失業率", metrics)


class TestPrompt(unittest.TestCase):

    def test_every_record_line_is_labelled(self):
        """每一行要帶地區與指標。

        踩過：`[_fmt(r) for r in records]` 只寫出「數值＋單位」，
        87 筆分區資料變成一串沒有標籤的裸數字，模型拿到了卻回答
        「現有數字無法告訴我們各行政區青年人口分布」。
        """
        recs = _district_records(PAYLOAD, "新北市", "18-35")[:5]
        prompt = build_prompt(PAYLOAD, recs, "測試問題", "新北市", "18-35")
        for r in recs:
            self.assertIn(r["region"], prompt)
            self.assertIn(r["metric"], prompt)

    def test_question_is_included(self):
        prompt = build_prompt(PAYLOAD, [], "預算該怎麼分配？", "新北市", "18-35")
        self.assertIn("預算該怎麼分配？", prompt)

    def test_prompt_forbids_inventing_numbers(self):
        """這條規則是整個設計的前提，不能被改掉。"""
        prompt = build_prompt(PAYLOAD, [], "問題", "新北市", "18-35")
        self.assertIn("只能使用下面列出的數字", prompt)
        self.assertIn("回答不了", prompt)


class TestAdviseVerification(unittest.TestCase):
    """用假後端塞已知答案，檢查查核有沒有正確分辨。"""

    def _advise_with(self, answer: str):
        return advise("測試問題", PAYLOAD, StubBackend({"__default__": answer}))

    def test_quoting_real_numbers_passes(self):
        g = self._advise_with("新北市 18-35 歲青年共 832,214 人。")
        self.assertTrue(g.trustworthy, g.summary())

    def test_invented_number_is_caught(self):
        """編造的數字一定要被抓到 —— 這是整個機制存在的理由。"""
        # 這個測試吃真的 unified.json。挑數字時先確認它真的不在池子裡 ——
        # 先前用 38.2%，後來接進居住資料後臺南市貸款負擔率剛好是 38.3%，
        # 「編造」變成了「引用」，測試就誤炸了。
        from llm.synthesize import _spellings
        pool = {round(p, 1) for r in PAYLOAD["records"] for p, pct in _spellings(r["value"], r["unit"]) if pct}
        for blk in PAYLOAD["benchmark"]["metrics"].values():
            if blk.get("unit") == "%":
                pool |= {round(v * 100, 1) for v in blk["values"].values()}
        # 提示詞裡的驅動模型／驗證／租金行也進查核池，所以最後用 verify 本人確認「真的對不上」
        from llm.advise import _drivers_lines
        from llm.synthesize import verify
        extra = _drivers_lines(PAYLOAD, "新北市", "青年租金負擔率")
        fake = next(f"{x / 10:.1f}" for x in range(200, 1000)
                    if not any(abs(x / 10 - p) <= 0.5 for p in pool)
                    and verify(f"{x / 10:.1f}%", [], PAYLOAD, extra=extra)[1])
        g = self._advise_with(f"青年租金負擔率高達 {fake}%，居六都之冠。")
        self.assertFalse(g.trustworthy, g.summary())
        self.assertIn(fake, g.unverified)

    def test_numbered_list_answer_is_not_falsely_flagged(self):
        """模型用編號清單回答時不該被誤報。"""
        g = self._advise_with("建議如下：\n1. 第一項\n2. 第二項\n3. 第三項")
        self.assertTrue(g.trustworthy, f"誤報了：{g.unverified}")

    def test_citing_a_policy_card_is_not_flagged(self):
        """提示詞允許引用施政建議卡片，查核池就必須認得那些數字。"""
        notes = PAYLOAD.get("policy_notes") or []
        self.assertTrue(notes, "這個測試需要 unified.json 裡有施政建議")
        nums = _numbers_in(notes[0].get("body", ""))
        if not nums:
            self.skipTest("第一張卡片的內文沒有數字可引用")
        g = self._advise_with(f"根據既有分析，{nums[0]} 這個數字值得注意。")
        self.assertTrue(g.trustworthy, f"引用卡片數字被誤報：{g.unverified}")


if __name__ == "__main__":
    unittest.main()
