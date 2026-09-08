# CLAUDE.md — 給 Claude Code 的專案說明

## 這是什麼

2026 新北市 AI 智慧城市黑客松參賽作品「YouthLens」的**資料層**。
題目：青年族群公開統計資料之連結整合與視覺化。

完整規劃在上層資料夾的 `YouthLens_Spec_2.md`，命題原文是同資料夾的 PDF。

⚠️ **Spec §9.1 寫的 A／B 兩人分工已經作廢 —— 三層全部由同一個人做。**
所以第二三層不是「別人的事」，是還沒做的事。

```
第三層  AI 中文問答        ⬜ 未做
第二層  視覺化儀表板       🟡 preview.html 是雛形，要長成這個
第一層  資料對齊引擎  ★    ✅ 完成
```

**因此 `preview.html` 不是拋棄式的檢查工具，它是儀表板的種子。**
它已經會讀 `unified.json`、有篩選器、有信心度標記、點得開資料履歷 ——
不要另外從零開始寫前端，在它上面長。

核心問題：政府各機關的年齡分組都不一樣（15-24 / 未滿25 / 五歲一組），
沒有一個切在《青年基本法》的 18–35 歲。引擎負責把它們對齊，
並誠實標示每個數字的推估信心度。

## 指令

```powershell
$env:PYTHONIOENCODING="utf-8"
$py="$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"

& $py -m unittest discover -s tests -v   # 41 個測試，改任何東西都要跑
& $py demo_align.py                      # 四幕現場 demo
& $py data/backtest.py                   # 拆組方法的實測誤差
& $py data/build_reference.py            # 重抓政府資料（加 --refresh 強制更新）
& $py data/build_unified.py              # 產出 unified.json（交給前端的檔）
& $py data/make_preview.py               # 產出 preview.html（雙擊即可開）
& $py demo_scan.py                       # 掃描檔判讀 demo
& $py -m llm.backend                     # 列出可用的 Bedrock 模型（9/12 早上第一件事）
& $py check_scan_accuracy.py --self-test # 驗證評分程式本身
node tests/verify_dashboard.mjs          # 驗證儀表板問答（改前端後一定要跑）
& $py check_scan_accuracy.py <圖片>       # 9/12 接上 Bedrock 後測真實準確率
```

## 環境陷阱（每次換電腦都會中）

1. Windows 上 `python` 常指向 Microsoft Store 的殼，**一律用上面的完整路徑**
2. PowerShell 印中文會亂碼，**先設 `PYTHONIOENCODING`**
3. 所有指令都要在 `youthlens/` 資料夾裡跑，不是上層

## 架構的硬性規則

**`engine/` 是純函式層。** 不碰網路、不讀檔（除了 reference.py 載入那一個 JSON）、
不呼叫 LLM。所有 I/O 都在 `data/` 與 `llm/`。這條界線不要打破。

**AI 只做「看懂」，不做「計算」。** `llm/` 負責看懂掃描檔、看懂中文問題，
回傳的一律是**結構**（年齡區間、意圖），不是數值。算數字一律走 `engine/`。
這是整個專案的架構主張，也是被評審追問時的答案 —— AI 碰不到數字，
所以它不可能算錯或編造。不要為了方便讓模型直接回傳計算結果。

**零第三方套件，只有 `llm/` 例外。** `engine/` 與 `data/` 只用標準函式庫。
`llm/` 接 Bedrock 需要 `pip install anthropic`（比賽規定只能用 Bedrock／SageMaker
的模型，不能直接呼叫 Anthropic API）。**沒裝也不影響其他部分執行。**

**比率型指標絕對不能乘權重。** 失業率 × 0.98 在統計上無意義。
`align_extensive()` 收到 `MetricKind.INTENSIVE` 會丟 `ValueError`，
這是特意的，不要「修」掉它。比率型走公式 C 或 D。

**參考資料只從 `data/reference_ntpc.json` 讀。** 換資料就換這個檔，
`engine/` 以上不用改任何一行。`reference_placeholder.json` 只剩測試在用。

## 檔案地圖

```
engine/
  schema.py     AgeBand / MetricKind / Provenance / AlignedRecord  ← 與前端的介面（Spec §7.3）
  reference.py  P(a) 單一年齡人口、r(a) 分齡發生率的查表層
  align.py      四種對齊公式 + 信心度判定                          ← 核心
data/
  sources.py           三個政府資料源的網址與快取
  fetch_population.py  戶政司 ODRP014 → 單一年齡人口（自動找最新一期）
  fetch_labour.py      主計總處 → 五歲組勞參率、失業率
  odsreader.py         用標準函式庫讀 ODS（政府統計表的通用格式）
  fetch_employment.py  主計總處表32 → 縣市 × 教育程度 × 五歲組就業者
  fetch_salary.py      主計總處表6 → 縣市 × 年齡別平均／中位數年薪
  ungroup.py           五歲組 → 單一年齡（PCHIP + 組內校準）
  backtest.py          量測 ungroup 的實際誤差 ← 信心度規則的依據
  build_reference.py   組合三者 → reference_ntpc.json
  build_unified.py     跑引擎 → unified.json（交付給前端）
  preview_template.html / make_preview.py  → preview.html ← 儀表板就長在這
llm/            唯一呼叫語言模型的地方，可抽換後端
  backend.py       StubBackend（賽前測試用）／ BedrockBackend（比賽當天）
  scan_table.py    掃描檔 → 結構化表格 ＋ 三道查核
demo_align.py   終端機版 demo（技術細節用），上台主要看 preview.html
demo_scan.py    掃描檔 demo，第二幕演「AI 讀錯時我們抓得到」
tests/          41 個測試
```

## 資料來源（免金鑰）

| 來源 | 內容 | 粒度 |
|---|---|---|
| 內政部戶政司 ODRP014 | 單一年齡人口 | **每一歲**、村里別、每月 |
| 主計總處 mp04020 | 分齡勞動力參與率 | 五歲組、全國、1978– |
| 主計總處 mp04031 | 分齡失業率 | 五歲組、全國、1978– |
| 主計總處 表32 | 縣市別就業者（含教育程度） | 五歲組、縣市、年度 |
| 主計總處 表6 | 縣市別平均／中位數年薪 | 未滿25 / 25-29 / 30-39…、縣市 |

### ODS 來源（已接進來，2026-09-08）

**用 `data/odsreader.py` 解析，只靠 `zipfile` + `xml.etree`，不要為此裝套件。**

| 表 | 內容 | 網址 |
|---|---|---|
| 主計總處 表6 | **縣市別 × 年齡別 全年總薪資**（平均數／中位數，含 6 個年度） | `https://ws.dgbas.gov.tw/001/Upload/463/relfile/11753/232642/` + URL-encode 的檔名 `表6　工業及服務業全年總薪資統計－本國籍全時受僱員工按工作場所所在縣市別及年齡別分.ods` |
| 主計總處 表32 | **縣市別 × 教育程度 × 年齡 就業者**（單位千人） | `https://ws.dgbas.gov.tw/001/Upload/463/relfile/11516/236078/table32.ods` |
| 主計總處 表29 | 縣市別 × 年齡組別勞參率 —— **尚未接入** | `https://ws.dgbas.gov.tw/001/Upload/463/relfile/11516/236078/table29.ods` |

年報總表在 `https://www.stat.gov.tw/News_Content.aspx?n=4001&s=236078`（114 年），
換年份時從 `https://www.stat.gov.tw/News.aspx?n=4001&sms=11516` 找新的 `s=` 編號。

**已驗證的關鍵事實：**

- 表6 的年齡分組是 `未滿25 / 25-29 / 30-39 / 40-49 / 50-64 / 65+`
  → **25-29 與我們的目標分組完全吻合，可標 high**。新北市 113 年平均 59.9 萬/年。
- 表32 實際上細到**五歲組**（15-24 / 25-29 / 30-34 / 35-39 / 40-44 / …），
  比原本以為的 25-44 細很多。新北市就業者 2,076 千人，25-29 歲 215 千人。
  同時帶教育程度，**是 Spec P1-1「教育程度 × 薪資」交叉的來源**（尚未做）。
- 表29 有**縣市別**的分齡勞參率，細到 25-29、30-34。接進來可以部分解除
  「死穴 2 只有全國資料」的限制，但**多層表頭欄位容易對錯，接之前先用
  臺灣地區那一列跟 mp04020 的全國值對答案**（25-29 應為 92.71%）。

**兩支 fetcher 都內建版面加總檢查**（`_verify()`），欄位位移會直接 raise。
年度改版後第一件事就是單獨執行它們，確認還讀得到正確的欄位。

**平均薪資是比率型，怎麼處理的：** 表6 的 `25-29` 直接對上（high）。
`30-35` 要從 `30-39` 切，原本卡在沒有匹配的分母 —— 解法是用
**受僱人數（人口 × 勞參率）當權重**，透過 `ungroup()` 把組值拆成單一年齡再重新聚合。
可以這樣做是因為薪資隨年齡**單調上升**（48.3 → 59.9 → 69.0 → 77.2），
沒有失業率那種局部高峰，屬 backtest 驗證為可靠的情況 → medium。
驗證：30-35 算出 66.8 萬，低於來源 30-39 的 69.0 萬（扣掉了較高薪的 36-39 歲），方向正確。

`data/_cache/` 有 38MB 原始檔，不進版控，跑一次 `build_reference.py` 就重建。
`reference_ntpc.json` 與 `unified.json` **有**進版控 —— 比賽現場可離線 demo。

## 改前端之後一定要跑 `node tests/verify_dashboard.mjs`

儀表板的問答是純前端，Python 測試涵蓋不到。這支會把 `preview.html`
裡**整段**腳本原封不動在假 DOM 上跑一次。

**關鍵是「什麼都不餵」**：不注入 M、R、BANDS、METRICS 等變數，
讓腳本自己宣告。先前的版本把這些當參數餵進去，等於幫程式補上了
其實沒宣告的變數，結果 `METRICS is not defined` 在測試裡永遠不會出現，
卻在真實瀏覽器裡直接讓按鈕失效。**驗證環境比真實環境寬鬆，等於沒驗證。**

`runAsk` 另外包了 try/catch，出錯會把訊息印在答案區而不是靜靜消失。

## 三處刻意偏離 Spec（都已驗證，不要改回去）

1. **結構轉折的信心度降級改成條件式。** §5.8 那條規則是為公式 A 寫的。
   公式 B 的 r(a) 曲線本身就在 18 歲跳 2.3 倍，再降一次是重複懲罰。
   見 `BREAK_CAPTURE_RATIO`。
2. **比率型強制要求分子分母。** 缺一就丟 `ValueError`。ETL 抓失業率時
   必須連失業人數與勞動力人數一起抓。
3. **§5.5「18–21 歲失業率約 1.4 倍」不成立。** 接上真實資料實測是 1.00 倍。
   **上台不要講 1.4 倍。**

## 回測結論（`data/backtest.py`）

| 曲線 | 我們的誤差 | 什麼都不做 | 結論 |
|---|---|---|---|
| 勞參率 | 2.49pp | 8.30pp | ✅ 降到 30% |
| 失業率 | 0.76pp | 0.54pp | ❌ 比不做更差 |

失業率在 20-24 歲有高峰，合併後峰消失。引擎已據此把「形狀有局部極值」
自動降為 low，見 `SHAPE_EXTREMUM_TOLERANCE`。**這兩個結論都鎖成測試了**，
如果哪天測試失敗，代表結論變了，要重新檢視降級規則。

## 比賽當天（9/12）切換到 Bedrock

AWS 環境**只在 9/12 08:00 – 9/13 13:00 開放**，賽前碰不到。
所有邏輯已用 `StubBackend` 測完（66 個測試），當天只要換設定：

```powershell
$env:YOUTHLENS_LLM_BACKEND = "bedrock"
$env:YOUTHLENS_AWS_REGION  = "us-east-1"        # 當天確認實際區域
$env:YOUTHLENS_BEDROCK_MODEL = "anthropic.claude-opus-5"   # 當天確認實際可用模型
```

**掃描檔準確率怎麼驗證：** `tests/fixtures/` 裡有一張測驗卷與標準答案。
標準答案來自 `data/fetch_employment.py` 解析的官方 ODS（已通過三道加總檢查），
測驗卷是同一份數值排版成統計表的 HTML。把它截圖存成 PNG，
用 `check_scan_accuracy.py <圖片>` 就能得到逐格準確率 —— 這是「怎麼知道 AI 讀對了」的答案。

**第一件事先跑 `python -m llm.backend`** —— 它會列出這個帳號實際可用的模型。
Workshop 帳號有哪些模型、開在哪一區，賽前無法得知，一跑就知道，不要猜。

## 現在的狀態

✅ 對齊引擎（四種切法 + 三道信心度檢查）
✅ 五個真實政府資料源全部接入，零第三方套件
✅ 回測驗證，結論鎖進測試
✅ `unified.json` 372 筆：30 個地區 × 4 個年齡層 × 6 個指標
✅ `preview.html` 可雙擊開啟的預覽頁
✅ 掃描檔判讀（AI 讀圖 ＋ 三道查核，含合計比對抓錯字）
✅ 66 個測試

⬜ 教育程度 × 薪資交叉（Spec P1-1，表32 已有教育程度欄位，就差組合）
⬜ 缺工趨勢預測（命題明列的預期成果，48 年序列已備妥）
⬜ 表29 縣市別分齡勞參率（可提升信心度，但表頭要小心）
⬜ 中文問答（AI 聽懂問題，數字仍由引擎查）
⬜ LLM 語意判讀（Spec §5.6，「社會新鮮人」→ 年齡區間）
⬜ 一鍵 ETL：把 build_reference + build_unified + make_preview 串成一支
