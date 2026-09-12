"""政策問答。—— 「根據這些資料，我這個政策該怎麼調整？」

跟 `data/build_unified.py::_policy_notes()` 的關係，是**疊加不是取代**：

    _policy_notes()   規則寫死，五條，永遠一樣，離線可用，完全可稽核
    advise()          針對使用者當下問的那個問題組織答案

為什麼不直接讓模型自由發揮寫政策建議：`_policy_notes()` 的註解已經
講過理由 —— 模型很會寫「建議加強社會住宅供給」，但那句話背後可能
一個數字都沒有，而且沒有人查得出來。`demo_ai.py` 幕三就是這種例子。

所以這裡的設計是：**規則先產生可稽核的發現，模型只負責在那些發現上
做推理與組織，然後每一個數字再驗一次。**

    1. 撈出跟問題相關的記錄（reuse synthesize.retrieve）
    2. 連同「規則算出來的施政建議」與「統計檢定過的洞察」一起餵給模型
    3. 模型寫出針對問題的建議
    4. **verify() 逐一比對每個數字**，對不上就標出來

模型的自由度在「怎麼組織、怎麼權衡、建議做什麼」，不在「數字是多少」。
這條界線跟掃描檔判讀那邊是同一條：AI 只做看懂與表達，不做計算。
"""

from __future__ import annotations

from .backend import Backend
from .synthesize import Grounded, _fmt, retrieve, verify

# 政策問題常常橫跨多個面向（就業＋薪資＋人口），撈窄了模型會無話可說
RECORD_LIMIT = 32


SIMILAR_CUE = ("下一個", "潛力", "候選", "相似", "類似", "像", "條件")


def _drivers_lines(payload: dict, region: str, question: str) -> list[str]:
    return _drivers_block(payload, region, question)[0]


def _drivers_block(payload: dict, region: str, question: str) -> tuple[list[str], str]:
    """六都驅動模型（data/drivers.py）給模型看的摘要：係數、本市殘差、跟某區條件最像的區。
    全部是程式算好的數字，模型只能引用；查核池也認得這些行。
    第二個回傳值是「這題怎麼答」的提示：問「下一個 X」時直接點名候選區，模型才不會把 X 本身當答案。"""
    D = (payload.get("trends") or {}).get("drivers")
    hint = ""
    if not D or not D.get("models"):
        return [], hint
    mb, mf = D["models"]["base"], D["models"]["full"]
    sig = lambda c: "顯著" if c["significant"] else "不顯著"
    lines = []
    V = (payload.get("meta") or {}).get("validation_migration") or {}
    B = (payload.get("meta") or {}).get("backtest_migration") or {}
    if V.get("r") is not None:
        lines.append(f"方法驗證：世代淨遷入（18–35 歲）跟戶政司登記的全年齡遷入−遷出，{V['n']} 個行政區跨區相關 r = {V['r']}，方向一致 {V['same_sign']}/{V['n']}")
    p23 = (B.get("pooled") or {}).get("2023") or (B.get("pooled") or {}).get(2023)
    if p23:
        lines.append(f"回測：假裝在 2023 年只用當時的資料，標「移入」的區有 {round(p23['inflow_precision'] * 100)}% 隔兩年真的淨移入、真的移入的區有 {round(p23['inflow_recall'] * 100)}% 被提前標到（六都 158 區）；點預測誤差跟沿用去年值差不多，價值在方向")
    lines += [f"六都 {mb['n']} 個行政區的迴歸（近三年青年淨遷入率 vs 條件，縣市固定效果，R² {mb['r2']}；係數是相關不是因果）："]
    for f in mb["features"]:
        c = mb["coef"][f]
        lines.append(f"  - {c['label']}高 10% 的區，淨遷入率高 {c['per_10pct']:+.2f} 個百分點（t {c['t']}，{sig(c)}）")
    c = mf["coef"]["ln_rent"]
    lines.append(f"  - 每坪月租高 10% → {c['per_10pct']:+.2f} 個百分點（模型 B，只含租金樣本足的 {mf['n']} 區，{sig(c)}）")
    fe = mb["coef"].get("fe_臺北市")
    if fe:
        lines.append(f"  - 臺北市固定效果 {fe['b']:+.1f} 個百分點：同樣條件下臺北的區青年淨流出較多（模型沒有房價變數）")
    # 租金 × 移入：點名的區＋預設六區，租金 2023→最新 的漲幅與淨遷入率的起訖
    rent_t = (payload.get("trends") or {}).get("rent") or {}
    mig_t = (payload.get("trends") or {}).get("migration") or {}
    if rent_t.get("series") and mig_t.get("areas"):
        q0 = question.replace("台", "臺")
        want = [region + n for n in ("新莊區", "淡水區", "林口區", "汐止區", "三峽區", "土城區")]
        want += [a for a in mig_t["areas"] if a != region and a.replace(region, "") in q0 and a not in want]
        rows = []
        for a in want:
            rs, ns = rent_t["series"].get(a), (rent_t.get("n") or {}).get(a)
            m = mig_t["areas"].get(a)
            if not rs or not m or not m.get("rate"):
                continue
            pts = [(y, v) for y, v, n in zip(rent_t["years"], rs, ns or [0] * len(rs)) if v is not None and n >= rent_t.get("min_n", 30)]
            rates = [(y, r) for y, r in zip(mig_t["years"], m["rate"]) if r is not None]
            if len(pts) < 2 or len(rates) < 2:
                continue
            (y0, r0), (y1, r1) = pts[0], pts[-1]
            rows.append(f"  - {a.replace(region, '')}：每坪月租 {y0} 年 {r0:,} → {y1} 年 {r1:,} 元（{(r1 / r0 - 1) * 100:+.0f}%）；"
                        f"淨遷入率 {rates[0][0]} 年 {rates[0][1] * 100:+.1f}% → {rates[-1][0]} 年 {rates[-1][1] * 100:+.1f}%（{m.get('signal') or '—'}）")
        if rows:
            lines.append("租金 × 移入（實價登錄租賃 2023 起才有足夠樣本；租金漲、移入降＝租金推力；租金低、移入強＝便宜拉力）：")
            lines += rows
    mine = [d for d in D["districts"] if d["city"] == region and d.get("resid") is not None]
    if len(mine) >= 5:
        hi = sorted(mine, key=lambda d: -d["resid"])[:3]
        lo = sorted(mine, key=lambda d: d["resid"])[:3]
        lines.append(f"{region}各區「實際移入 − 條件解釋的移入」（正＝條件之外還有拉力；負＝條件好但青年沒來）：")
        lines.append("  - 高於條件：" + "、".join(f"{d['short']} {d['resid']:+.1f} 個百分點（實際 {d['y']:+.1f}%）" for d in hi))
        lines.append("  - 低於條件：" + "、".join(f"{d['short']} {d['resid']:+.1f} 個百分點（實際 {d['y']:+.1f}%）" for d in lo))
    # 條件最像的區：問題點到的區優先（本市先），否則問到「下一個／潛力／像」時用淡水當參考
    q = question.replace("台", "臺")
    ref = None
    for d in sorted(D["districts"], key=lambda d: d["city"] != region):
        if d.get("z") and d["short"] and d["short"] in q:
            ref = d; break
    if ref is None and any(k in q for k in SIMILAR_CUE):
        ref = next((d for d in D["districts"] if d["area"] == "新北市淡水區" and d.get("z")), None)
    if ref:
        keys = D["similarity"]
        mig = ((payload.get("trends") or {}).get("migration") or {}).get("areas") or {}
        cands = []
        for d in D["districts"]:
            if d is ref or not d.get("z") or d.get("y") is None:
                continue
            dist = sum((d["z"][k] - ref["z"][k]) ** 2 for k in keys) ** 0.5
            cands.append((dist, d))
        cands.sort(key=lambda t: t[0])

        def _line(dist, d):
            s = (mig.get(d["area"]) or {}).get("signal") or ("近三年淨移入" if d["y"] > 0 else "近三年淨流出")
            stage = "已經在移入" if d["y"] > 0.5 else ("條件像但移入還沒起來" if d["y"] > -1 else "條件像但仍在流出")
            return (f"  - {d['area']}：距離 {dist:.2f}，近三年淨遷入 {d['y']:+.1f}%（{s}；{stage}），"
                    f"租 {d['rent']} 元/坪、密度 {d['jobs_density']}、所得 {d['income']} 萬")

        head = (f"{ref['area']}（近三年淨遷入 {ref['y']:+.1f}%，租 {ref['rent']} 元/坪、工作機會密度 {ref['jobs_density']}、"
                f"所得中位數 {ref['income']} 萬）")
        local = [(dist, d) for dist, d in cands if d["city"] == region][:5]
        if local:
            lines.append(f"{region}內條件最像 {head} 的區（相對本市租金、工作機會密度、所得、青年人口規模四維標準化距離）：")
            lines += [_line(dist, d) for dist, d in local]
        lines.append(f"六都內條件最像 {ref['area']} 的區：")
        lines += [_line(dist, d) for dist, d in cands[:6]]
        if any(k in q for k in SIMILAR_CUE):
            nxt = [d["short"] for _, d in local if -1 < d["y"] <= 0.5] or [d["short"] for _, d in local if d["y"] <= 0.5]
            already = [d["short"] for _, d in local if d["y"] > 0.5]
            hint = (f"這題問的是「下一個」：答案要從「{region}內條件最像 {ref['short']}」清單裡挑"
                    f"**條件像但移入還沒起來**的區（{'、'.join(nxt) if nxt else '清單裡沒有，就照實說'}），"
                    f"不要回答 {ref['short']} 本身，也不要回答現在移入最多的區。"
                    + (f"{'、'.join(already)} 已經在移入，可以拿來驗證這組條件有效。" if already else "")
                    + (f" 並提醒：{ref['short']} 實際移入比這四個條件解釋的高 {ref['resid']:+.1f} 個百分點，多出來的可能來自住宅供給與交通建設（模型沒有這兩個變數），所以條件像只是必要條件。" if ref.get("resid") is not None else ""))
    return lines, hint


def build_prompt(payload: dict, records: list[dict], question: str,
                 region: str, band: str) -> str:
    """組出提示詞。**可用的數字全部列進去，模型只能挑，不能算也不能編。**

    每一行都要帶地區、年齡層、指標與可靠度。`_fmt()` 只回傳「數值＋單位」——
    先前這裡直接 `[_fmt(r) for r in records]`，等於餵給模型一串沒有標籤的
    裸數字。實測後果：87 筆行政區資料全部進了提示詞，模型卻回答
    「現有數字無法告訴我們各行政區青年人口分布」—— 它看得到數字，
    但不知道哪個數字屬於哪一區。
    """
    lines = []
    for i, r in enumerate(records, 1):
        edu = f"／{r['education']}" if r.get("education") else ""
        # 「全體」的口徑照來源寫：家戶（居住）、申報戶（所得）、全體（普查工作機會）
        who = (f"{r['provenance'].get('source_age_group') or '全體'}（非青年）"
               if r["age_group"] == "全體" else f"{r['age_group']} 歲")
        lines.append(f"[{i}] {r['region']}{edu} {who} "
                     f"{r['metric']} = {_fmt(r)} "
                     f"（可靠度 {r['provenance']['confidence']}）")

    notes = payload.get("policy_notes") or []
    insights = payload.get("insights") or []

    # 六都比較：每個指標六個城市的值與名次都給。「六都誰最重、本市排第幾」這種問題
    # 的答案本來就在 payload 裡，先前提示詞沒放，模型只能說回答不了。
    bench = payload.get("benchmark") or {}
    bench_text = ""
    if bench.get("metrics"):
        def _bv(v, unit):
            return f"{v * 100:.1f}%" if unit == "%" else (f"{v:.2f} 倍" if unit == "倍" else f"{v:.1f} {unit}")
        rows = []
        for metric, blk in bench["metrics"].items():
            order = sorted(blk["values"], key=lambda c: blk["rank"].get(c, 99))
            rows.append(f"  - {metric}" + (f"（{blk['scope']}）" if blk.get("scope") else "")
                        + "：" + "、".join(f"{c} {_bv(blk['values'][c], blk['unit'])}（第 {blk['rank'][c]}）" for c in order))
        bench_text = ("\n**六都比較（" + str(bench.get("band", "25-29"))
                      + " 歲官方公布值；房價所得比、貸款負擔率為全體家戶）**\n"
                      + "\n".join(rows) + "\n")

    drv, drv_hint = _drivers_block(payload, region, question)
    drv_hint = f"\n**這題怎麼答**\n{drv_hint}\n" if drv_hint else ""
    drivers_text = ("\n**六都驅動模型與條件比對（程式算好的迴歸結果，可直接引用）**\n" + "\n".join(drv) + "\n") if drv else ""

    def block(title: str, items: list[dict], keys: tuple[str, str]) -> str:
        if not items:
            return ""
        # detail 是卡片的細項（行業名稱、數字），不給的話模型只看到「這幾個領域」
        # 卻不知道是哪幾個，實測它會因此回答「資料回答不了」。
        body = "\n".join(
            f"  - {it.get(keys[0], '')}（信心度 {it.get('confidence', '?')}）\n"
            f"    {it.get(keys[1], '')}"
            + "".join(f"\n      · {d}" for d in (it.get("detail") or [])[:4])
            for it in items[:8]
        )
        return f"\n{title}\n{body}\n"

    return f"""你是{region}青年事務的政策幕僚。承辦人問了你一個問題，
請根據下面的資料回答。

**使用者的問題**
{question}
{drv_hint}
**最重要的規則：你只能使用下面列出的數字。**
不可以自己計算、不可以推估、不可以寫出清單裡沒有的數字。
需要比較大小、講趨勢方向、給出行動建議都可以，
但任何具體數值都必須出自下面的清單。

**只有**清單與卡片裡完全沒有相關數字時，才說「這個問題現有資料回答不了」並說明缺哪一種資料。
有部分相關的數字時，**第一句先用它們回答**，再講口徑限制（全國／全年齡／家戶）——
不要先說回答不了、然後又引用一堆數字。**不要為了看起來有幫助而編一個數字。**

標成「全體家戶（非青年）」的數字是家戶層級的官方值。問到居住、買房、房貸時
可以引用它們，但要講明這是全體家戶的數字，而青年所得低於家戶中位數，
實際負擔只會更重 —— 這是「有資料但口徑不同」，不是「沒有資料」。
清單裡有這兩個數字時，**不要**說「回答不了」：先用它們回答（家戶要幾年不吃不喝、
月所得幾成拿去繳房貸），講明口徑，再給建議。

同樣地，行業（職缺、缺工、供需錯配、行業薪資）的數字是**全國、全年齡**的 —— 官方**任何縣市都沒有**
行業別的職缺或分齡就業表（不是{region}特別缺，六都都一樣；我們逐一查過主計總處 67 張表）。
問到行業時就直接用全國訊號給出具體建議，一句話講明「全國訊號」即可；
**不要把「{region}沒有行業別資料」列成缺口或當成不能回答的理由**，那是所有縣市的共同限制。
施政建議卡片底下的「·」細項就是具體行業與數字，可以直接引用。

**可用的數字（{region}．{band} 歲為主{"；問題點名的其他城市／行政區排在最前面" if payload.get("_cross_city") else ""}）**
{("⚠ 這是跨城市的問題：問到的地區是 " + "、".join(payload["_cross_city"]) + "，請用它們各自的數字比較，不要拿" + region + "當替身。") if payload.get("_cross_city") else ""}
{chr(10).join(lines)}
{bench_text}{drivers_text}{block("**規則算出來的施政建議（已附依據，可直接引用）**", notes, ("title", "body"))}
{block("**通過統計檢定的變化（已附依據，可直接引用）**", insights, ("title", "body"))}
問「下一個淡水」「哪區有潛力」「哪區條件像 X」時，**答案是「條件最像」清單裡標「條件像但移入還沒起來」的區**，
不是現在移入最多的區（那是「現在的淡水」，不是「下一個」）。先講本市內的候選，再補六都的對照；
「已經在移入」的區用來驗證這組條件有效。每個區附它的條件數字與近三年淨遷入率；
迴歸係數說「相關」不說「因為」；提醒淡水本身的移入有 3.5 個百分點是條件解釋不了的（新市鎮住宅供給、輕軌），
所以「條件像」只是必要條件，還要看住宅供給與交通建設。

**怎麼回答**
- **第一行只寫一句結論**（40 字內，不要標題、不要「以下是」），畫面上只先顯示這一句；
  空一行之後才是完整分析
- 建議要具體到「做什麼」，不要停在「應重視」這種層次
- 每個建議後面接它依據哪個數字
- 三百字以內
- 有反面證據或不確定的地方要講出來，不要只講支持結論的部分
"""


def _mentioned_records(payload: dict, question: str, have: list[dict]) -> list[dict]:
    """問題裡點名的地區（包含其他城市、其他城市的行政區）的記錄，補進提示詞。

    lambda_handler 會把被點名城市的資料併進 payload；這裡負責把它們挑出來給模型。
    """
    q = (question or "").replace("台", "臺").replace("内", "內")
    if not q:
        return []
    seen = {(r["region"], r["age_group"], r["metric"], r.get("education")) for r in have}
    out = []
    for r in payload.get("records", []):
        reg = r.get("region", "")
        short = reg
        for c in ("新北市", "臺北市", "桃園市", "臺中市", "臺南市", "高雄市"):
            if reg.startswith(c) and reg != c:
                short = reg[len(c):]
        if reg == "全國" or not (reg in q or (short != reg and short in q)):
            continue
        if r.get("age_group") not in ("18-35", "全體", "25-29") or r.get("education"):
            continue
        key = (r["region"], r["age_group"], r["metric"], r.get("education"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _district_records(payload: dict, region: str, band: str) -> list[dict]:
    """各行政區的分區數字。

    政策問題十有八九跟「資源怎麼分配到各區」有關，而行政區人口正好是
    整份資料裡品質最高的一塊（戶政司單一年齡實數、精確加總、high）。
    `retrieve()` 只撈單一地區，所以模型看不到這些 —— 實測它因此回答
    「缺少各行政區的青年人口數」，但那份資料一直都在。

    只取有分區的三個指標；其餘指標沒有行政區層級，撈了也是空的。
    """
    wanted = {"人口數", "勞動力人數", "勞動力參與率", "青年人口年變化率", "青年淨遷入率", "青年淨遷入人數"}
    # 行政區層級的全體指標（財政部所得中位數、普查在地工作機會與密度）也給模型 ——
    # 「林口區的工作供需」靠的就是這些，先前模型只能回「沒有」。
    whole = {"綜合所得中位數", "工作機會密度", "在地工作機會", "住宅每坪月租中位數", "住宅月租金中位數"}
    out = [
        r for r in payload.get("records", [])
        if r.get("region", "").startswith(region)
        and r.get("region") != region
        and ((r.get("age_group") == band and r.get("metric") in wanted)
             or (r.get("age_group") == "全體" and r.get("metric") in whole))
    ]
    # 依人口排序，讓模型先看到最大的幾區
    out.sort(key=lambda r: -r.get("value", 0))
    return out


def advise(question: str, payload: dict, backend: Backend, *,
           region: str = "新北市", band: str = "18-35") -> Grounded:
    """回答一個政策問題，並驗證答案裡的每個數字。

    回傳的 `Grounded.unverified` 不為空時，代表模型寫出了資料不支持的數字 ——
    前端**必須**把這件事顯示出來，不能只印文字。
    """
    records = retrieve(payload, region, band, limit=RECORD_LIMIT)
    records += _district_records(payload, region, band)
    # 問題點名的地區（含其他城市）排到最前面：模型讀清單是從頭讀的，
    # 放在第 200 筆之後它會拿本市當替身來比（實測：問內湖 vs 林口，它拿臺南市比林口）
    records = _mentioned_records(payload, question, records) + records
    raw = backend.complete(build_prompt(payload, records, question, region, band))
    text = raw.strip()
    ok, bad = verify(text, records, payload, extra=_drivers_lines(payload, region, question))
    return Grounded(text=text, records=records, verified=ok, unverified=bad, raw=raw)
