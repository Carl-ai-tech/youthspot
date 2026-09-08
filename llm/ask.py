"""用中文問資料。

AI 在這裡只做一件事：**把一句中文變成一組選項**。

    「新北市 25-29 歲平均年薪多少」
        ↓  AI 只做這一步
    {地區: 新北市, 年齡: 25-29, 指標: 平均年薪}
        ↓  接下來完全是程式查表，AI 碰不到
    59.9 萬元／年（high）＋ 完整資料履歷

為什麼不讓 AI 直接回答數字：它會編。而且編出來的數字看起來很合理，
沒有人會發現。所以它只能從我們給的清單裡挑，挑不到就說挑不到。

Spec §7.2 也否決了 text-to-SQL —— 讓模型生 SQL 再執行，失敗率高，
而且錯了不會有人發現。受控意圖沒有這個問題：模型沒有自由發揮的空間。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .backend import Backend

UNANSWERABLE = "unanswerable"

# 排名沒指定年齡層時的預設。不設的話四個年齡層會混在一起排，
# 同一個行政區會出現四次，排出來的名次是錯的。
RANK_DEFAULT_BAND = "18-35"


@dataclass
class Vocabulary:
    """能問的東西就這些。從 unified.json 直接生出來，不是手寫的。"""

    regions: list[str] = field(default_factory=list)
    age_groups: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    educations: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict) -> "Vocabulary":
        meta = payload.get("meta", {})
        regions, seen = [], set()
        for r in payload.get("records", []):
            if r["region"] not in seen:
                seen.add(r["region"])
                regions.append(r["region"])
        return cls(
            regions=regions,
            age_groups=list(meta.get("age_groups", [])),
            metrics=list(meta.get("metrics", [])),
            educations=list(meta.get("educations", [])),
            industries=list(meta.get("industries", [])),
        )


@dataclass
class Intent:
    kind: str = "lookup"          # lookup 查一個數字／rank 比較排名／unanswerable
    region: str | None = None
    age_group: str | None = None
    metric: str | None = None
    education: str | None = None
    industry: str | None = None
    reason: str = ""              # 答不出來時說明為什麼


@dataclass
class Answer:
    text: str
    records: list[dict] = field(default_factory=list)
    intent: Intent | None = None
    answered: bool = True


def build_prompt(question: str, vocab: Vocabulary) -> str:
    """把可選項目全部列給模型。它只能挑，不能發明。"""
    def block(name: str, items: list[str]) -> str:
        return f"{name}：{('、'.join(items) if items else '（無）')}"

    return f"""你的工作是把一句中文問題，對應到下面清單裡的選項。

**你只能從清單裡挑，不可以自己發明選項，也絕對不要回答任何數字。**
數字由另一支程式去查，你的工作只是告訴它要查什麼。

可以查的地區　{block('', vocab.regions[:40])}
可以查的年齡層　{block('', vocab.age_groups)}
可以查的指標　{block('', vocab.metrics)}
可以查的教育程度　{block('', vocab.educations)}
可以查的行業　{block('', vocab.industries)}

kind 只能是這三個之一：
  lookup　　查一個特定的數字（例如「新北市 25-29 歲平均年薪」）
  rank　　　比較排名（例如「哪一區青年最多」「哪些行業最缺工」）
  unanswerable　問題超出上面清單能回答的範圍

**如果問的東西清單裡沒有，一定要回 unanswerable 並說明原因。
不要硬挑一個最接近的充數。**

問題：{question}

只輸出 JSON，不要其他文字：
{{"kind":"lookup","region":null,"age_group":null,"metric":null,
  "education":null,"industry":null,"reason":""}}"""


def _strip_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    return m.group(1) if m else text.strip()


def _pick(value: Any, allowed: list[str]) -> str | None:
    """模型只能挑清單裡的東西。挑了清單外的一律當沒挑。"""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value in allowed:
        return value
    for item in allowed:                       # 「板橋區」對應「新北市板橋區」
        if value in item or item in value:
            return item
    return None


def parse_intent(question: str, vocab: Vocabulary, backend: Backend) -> Intent:
    raw = backend.complete(build_prompt(question, vocab))
    try:
        payload = json.loads(_strip_fence(raw))
    except json.JSONDecodeError:
        return Intent(kind=UNANSWERABLE, reason="沒辦法解析這個問題")

    kind = str(payload.get("kind", "lookup"))
    if kind not in {"lookup", "rank", UNANSWERABLE}:
        kind = UNANSWERABLE

    return Intent(
        kind=kind,
        region=_pick(payload.get("region"), vocab.regions),
        age_group=_pick(payload.get("age_group"), vocab.age_groups),
        metric=_pick(payload.get("metric"), vocab.metrics),
        education=_pick(payload.get("education"), vocab.educations),
        industry=_pick(payload.get("industry"), vocab.industries),
        reason=str(payload.get("reason") or ""),
    )


def _fmt(rec: dict) -> str:
    unit = rec.get("unit", "")
    if unit == "%":
        return f"{rec['value'] * 100:.1f}%"
    if unit.startswith("萬"):
        return f"{rec['value']:,.1f} {unit}"
    return f"{rec['value']:,.0f} {unit}"


def _match(rec: dict, intent: Intent) -> bool:
    if intent.region and rec["region"] != intent.region:
        return False
    if intent.age_group and rec["age_group"] != intent.age_group:
        return False
    if intent.metric and rec["metric"] != intent.metric:
        return False
    edu = intent.education or intent.industry
    if edu and rec.get("education") != edu:
        return False
    if not edu and rec.get("education"):
        return False
    return True


def resolve(intent: Intent, records: list[dict], default_region: str) -> Answer:
    """把意圖變成答案。**這一段完全沒有 AI**，純粹查表。"""
    if intent.kind == UNANSWERABLE:
        return Answer(
            text=intent.reason or "這個問題超出我們手上資料能回答的範圍。",
            intent=intent, answered=False,
        )

    if not intent.metric:
        return Answer(text="我不確定你想查哪一個指標。", intent=intent, answered=False)

    if intent.kind == "rank":
        band = intent.age_group or RANK_DEFAULT_BAND
        pool = [r for r in records
                if r["metric"] == intent.metric
                and (r["age_group"] == band or r["age_group"] == "全體")
                and r["region"] != default_region
                and not r.get("education")]
        if not pool and intent.industry is None:
            pool = [r for r in records if r["metric"] == intent.metric and r.get("education")]
        if not pool:
            return Answer(text=f"手上沒有可以拿來比較的「{intent.metric}」資料。",
                          intent=intent, answered=False)
        ranked = sorted(pool, key=lambda r: r["value"], reverse=True)[:5]
        lines = [f"{i + 1}. {r.get('education') or r['region']}　{_fmt(r)}"
                 for i, r in enumerate(ranked)]
        label = "" if ranked[0]["age_group"] == "全體" else f"{band} 歲"
        return Answer(
            text=f"{label}{intent.metric}排名前 {len(ranked)} 名：\n" + "\n".join(lines),
            records=ranked, intent=intent,
        )

    hits = [r for r in records if _match(r, intent)]
    if not hits:
        return Answer(
            text=("這個組合我們沒有資料。"
                  + (f"（{intent.reason}）" if intent.reason else "")),
            intent=intent, answered=False,
        )

    rec = hits[0]
    who = rec["region"] + (f"　{rec['education']}" if rec.get("education") else "")
    conf = rec["provenance"]["confidence"]
    tail = "，這個數字沒有經過推估" if conf == "high" else "，這個數字經過推估（點開可看算法）"
    return Answer(
        text=f"{who}　{rec['age_group']} 歲的{rec['metric']}是 {_fmt(rec)}{tail}。",
        records=hits[:3], intent=intent,
    )


def ask(question: str, payload: dict, backend: Backend) -> Answer:
    """問一句中文，拿到答案 + 它引用的每一筆資料（都帶完整履歷）。"""
    vocab = Vocabulary.from_payload(payload)
    intent = parse_intent(question, vocab, backend)
    return resolve(intent, payload["records"], payload["meta"]["region"])
