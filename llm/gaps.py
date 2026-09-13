"""把回答中的資料缺口對照專案固定目錄，提供白話說明。

不對整篇回答做關鍵字搜尋，也不代表已即時查證外部資料可取得。
status/note 保留舊 API 相容；新畫面以 display_status/plain_note 區分
已使用、替代指標、需補充、候選來源。判斷規則固定，不額外呼叫模型。
"""

from __future__ import annotations

import re

CATALOG: list[dict] = [
    {"key": "行業 × 年齡的就業人數",
     "pattern": r"(行業|產業).{0,12}(青年|年齡|分齡).{0,12}(就業|從業|人數)|(青年|年齡).{0,8}(行業|產業).{0,8}(分布|就業|人數)",
     "status": "missing",
     "note": "官方沒有「行業 × 年齡」的表：114 年人力資源調查年報 44 張表、主計總處 mp04013–035 系列 23 張逐一看過。"
             "能用的是全國「就業者之行業」（全年齡，已接入供需錯配）。"},
    {"key": "行業招聘困難度／缺工程度",
     "pattern": r"招聘困難|招募困難|缺工程度|缺工率|職缺率|人力短缺|難以招募",
     "status": "available",
     "note": "已接入：主計總處事業人力僱用狀況調查的各業職缺數（mp05005，1997–）與趨勢預估；"
             "「缺工與供需錯配」那一格就是。職缺率（mp05006）在同一個目錄，可再接。"},
    {"key": "青年流出率／人口外流",
     "pattern": r"流出率|外流|人口流失|遷出|遷入|淨遷|移出",
     "status": "external",
     "note": "有但未接入：內政部戶政司 各鄉鎮市區遷入遷出人數（每月，分區）—— 跟人口用同一個 API 家族。"
             "分齡的遷徙官方沒有公布，只能看全年齡淨遷。"},
    {"key": "行政區的失業率／就業人數",
     "pattern": r"(各區|分區|行政區|區級|區的|區內).{0,10}(失業率|就業人數|就業者|失業)|(失業率|就業人數).{0,6}(各區|分區|行政區)",
     "status": "missing",
     "note": "人力資源調查是抽樣，樣本撐不到行政區，所以失業率／就業人數只有縣市層級。"
             "替代（已接入）：普查的「在地工作機會」與「工作機會密度」、戶政司推估的各區勞動力。"},
    {"key": "行政區的薪資",
     "pattern": r"(各區|分區|行政區|區級|區的).{0,10}(薪資|薪水|年薪|所得)|(薪資|薪水|年薪|所得).{0,6}(各區|分區|行政區)",
     "status": "available",
     "note": "薪資調查只有縣市；已接入的替代是財政部稅籍的各行政區「綜合所得中位數」（全體申報戶，非抽樣）。"},
    {"key": "通勤流向",
     "pattern": r"通勤|流向|跨區工作|工作地",
     "status": "external",
     "note": "有但未接入：主計總處人口及住宅普查的「工作地與居住地」交叉（110 年，鄉鎮市區）。"
             "目前用「工作機會密度」（在地從業員工 ÷ 15–64 歲人口）當代理指標，已接入。"},
    {"key": "行政區的租金",
     "pattern": r"(各區|分區|行政區|區級|區的|哪區|哪一區).{0,10}(租金|房租|月租|每坪)|(租金|房租|月租).{0,6}(各區|分區|行政區|水準|中位數)",
     "status": "available",
     "note": "已接入：內政部地政司實價登錄租賃案件，六都各行政區「住宅每坪月租中位數」「住宅月租金中位數」"
             "（近四季，全體案件非青年；110 年 7 月起只強制租賃住宅服務業與社宅申報，樣本偏向代管／包租物件）。"},
    {"key": "住宅供給（建照／使照）",
     "pattern": r"建照|使照|建造執照|使用執照|新建|推案|住宅供給|新成屋|蓋房",
     "status": "external",
     "note": "有但未接入：內政部國土管理署「核發建築物建造執照統計」在 data.gov.tw 只有縣市層級；"
             "鄉鎮市區層級在內政統計查詢網（需互動查詢）與 SEGIS「房屋稅籍住宅類數量依屋齡」（需帳號）。"
             "這是「下一個淡水」最缺的領先變數：房子先蓋、人 1–2 年後才到。"},
    {"key": "生育率／婚育",
     "pattern": r"生育率|出生數|生小孩|生育|婚育",
     "status": "available",
     "note": "已接入：各行政區 18–35 歲女性一般生育率（戶政司 ODRP056 出生數按生母單一年齡 ÷ 同區 18–35 歲女性人口，按發生）。"
             "結婚對數按年齡尚未接（戶政司 ODRP 結婚人數按年齡）。"},
    {"key": "青年租金補貼／社會住宅",
     "pattern": r"租金補貼|租補|社會住宅|社宅",
     "status": "external",
     "note": "有但未接入：內政部國土管理署租金補貼統計、新北市住都中心社宅出租統計。列在「權益覆蓋」的缺口清單。"},
    {"key": "初次尋職者",
     "pattern": r"初次尋職|新鮮人|應屆",
     "status": "external",
     "note": "有但未接入：主計總處 mp04029 歷年失業者按初次尋職者分（全國、分性別，1978–），固定網址 XML。"},
    {"key": "性別 × 年齡",
     "pattern": r"性別|男女|女性|男性",
     "status": "available",
     "note": "已接入：戶政司人口分齡 × 性別（各區）、主計總處全國分齡 × 性別勞參率／失業率（1978–）。"},
    {"key": "產業別的青年薪資",
     "pattern": r"(行業|產業).{0,8}(青年|年齡).{0,8}(薪資|薪水|年薪)|(青年|年齡).{0,8}(行業|產業).{0,8}(薪資|薪水|年薪)",
     "status": "missing",
     "note": "官方薪資統計有「行業別」（表1，已接入）與「年齡別」（表6，已接入），沒有兩者的交叉。"},
]

# 舊 status/note 保留給既有呼叫端；畫面使用 display_status/plain_note。
# 這些說明來自專案固定目錄，不代表本次即時查詢或確認外部資料可取得。
_DISPLAY = {
    "行業 × 年齡的就業人數": ("needed", "目前沒有可直接使用的青年分行業就業資料；已用全年齡行業資料作參考，仍需補充青年資料。"),
    "行業招聘困難度／缺工程度": ("available", "已使用各行業職缺數與趨勢，可先查看缺工與供需錯配；職缺數不等於職缺率。"),
    "青年流出率／人口外流": ("proxy", "已有世代人口變化推估的淨遷入訊號，但不是直接記錄青年搬入、搬出的人數。"),
    "行政區的失業率／就業人數": ("proxy", "已有各區工作機會密度與勞動力推估，可作參考；不能當成各區實測失業率。"),
    "行政區的薪資": ("proxy", "已有各區全體申報戶的所得中位數，可比較所得情況；不等於青年薪資。"),
    "通勤流向": ("proxy", "已有工作機會密度，但還不能回答青年住在哪一區、到哪一區上班。"),
    "行政區的租金": ("available", "已使用各區實價登錄租金中位數；涵蓋的是登錄案件，不代表所有青年租屋情況。"),
    "住宅供給（建照／使照）": ("candidate", "可再查建照、使照與住宅統計；是否涵蓋需要的行政區與年份，仍待確認。"),
    "生育率／婚育": ("proxy", "已有各區青年女性生育率，但不能代替結婚情況；結婚年齡資料尚待補充。"),
    "青年租金補貼／社會住宅": ("candidate", "可再查租金補貼與社宅統計；能否分出青年、行政區與需要的年份，仍待確認。"),
    "初次尋職者": ("candidate", "資料目錄列有全國初次尋職者統計；能否回答這次需要的地區與年份，仍待確認。"),
    "性別 × 年齡": ("available", "已使用各區分齡與性別人口，以及全國分齡與性別就業指標；地區範圍須分開看。"),
    "產業別的青年薪資": ("needed", "目前已有產業別與年齡別薪資，但沒有同時分產業和青年年齡的數字，還需要補充。"),
}

for _item in CATALOG:
    _item["display_status"], _item["plain_note"] = _DISPLAY[_item["key"]]

_TRIGGER = re.compile(r"建議.{0,6}(補|蒐集|收集|取得|調查|補充)|缺(少|乏|的資料|口)|需要.{0,10}(資料|數據)|資料限制|無法(取得|回答)|尚未(取得|掌握|蒐集)|待補(充)?")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.、)])\s*")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+|\*\*)")
_NEGATED_TRIGGER = re.compile(r"^(?:目前)?(?:並未|沒有|並不|不)(?:缺少|缺乏|需要補充)")
_POSITIVE_ONLY = re.compile(r"^(?:目前)?(?:已使用|已接入|已有|已取得|已掌握|不缺|無須補充|不需補充)")


def _gap_passages(text: str) -> list[str]:
    """只比對缺口句；僅明確缺口標題下的相鄰條列繼承缺口語意。"""
    passages = []
    gap_list = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        is_bullet = bool(_BULLET.match(line))
        clean = _BULLET.sub("", line).strip().strip("#* ")
        # 新標題與普通敘述都會結束上一組缺口條列。
        inherited = gap_list and is_bullet
        if not is_bullet:
            gap_list = False
        sentences = re.split(
            r"[。！？!?；;]|[，,](?=(?:但|不過|然而)?(?:目前)?(?:已使用|已接入|已有|已用|缺少|缺乏|需要|建議))",
            clean,
        )
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence or _NEGATED_TRIGGER.match(sentence):
                continue
            explicit = bool(_TRIGGER.search(sentence))
            if _POSITIVE_ONLY.match(sentence) and not explicit:
                continue
            if explicit or inherited:
                passages.append(sentence)
        # 標題本身不能靠「已用資料，仍有服務缺口」之類句子開啟繼承。
        header = clean.rstrip("：:")
        if not is_bullet and len(header) <= 30 and _TRIGGER.search(header):
            if line.endswith(("：", ":")) or _HEADING.match(line):
                gap_list = True
    return passages


def find_gaps(text: str) -> list[dict]:
    """對照回答中真正談到的資料缺口，不掃描其餘證據或分析段落。"""
    if not text:
        return []
    passages = _gap_passages(text)
    return [
        {field: item[field] for field in ("key", "status", "note", "display_status", "plain_note")}
        for item in CATALOG
        if any(re.search(item["pattern"], passage) for passage in passages)
    ]
