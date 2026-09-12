> 歷史紀錄：以下反映當時審查或部署狀態。最新整合與操作方式見 [部署說明](../../deploy/CLOUDFORMATION.md)；本輪已保留 Webber 最新功能並整合 YouthScope 品牌與私有 S3 部署。

# YouthScope 架構圖

以本機程式與部署腳本核對的 9 元件主架構。圖中 AWS 為程式支援的路徑，**現有腳本待合規修正，未驗證部署**。本文件不宣稱正式站、S3、Lambda 或 Bedrock 已上線。

## 交付物與閱讀方式

- `youthscope.html`：Archify 自包含 HTML，可由使用者在瀏覽器開啟。內建搜尋、縮放、節點聚焦、主題及匯出控制；本輪未完成按鈕互動驗證。
- `youthscope.architecture.json`：typed JSON 原始規格，已冻结。
- `youthscope.svg`：從已交付 HTML 的 inline SVG 抽出並帶入樣式，可作向量素材；不是經 Viewer 匯出按鈕產生。
- `youthscope.visual-check.*.png`：1440×900 與 2048×1320、明暗主題的官方瀏覽器截圖。
- `youthscope.visual-check.html`：截圖對照；`delivery.json`、`youthscope.visual-check.json`、`manual-review.json`：可追溯驗證紀錄。

內容使用繁體中文。Archify 2.17 只支援 en／zh-CN 的固定 Viewer UI，所以控制列與 HTML lang 回退英文；沒有把繁中內容改成簡中。

## 技術含義與代碼依據

| 圖中元件／路徑 | 依據與範圍 |
| --- | --- |
| 政府資料 → ETL | `data/fetch_*.py`、`data/sources.py`、`data/build_reference.py`；部分資料需先人工取得，不表示全部來源均為即時 API。 |
| 對齊與統計預測 | `engine/align.py`、`engine/reference.py`、`engine/schema.py` 處理口徑；`data/forecast.py`、`data/build_unified.py` 處理統計預測與彙整，forecast 不在 engine 目錄。 |
| 統計 → unified.json | `run_pipeline.py` 寫入 `data/unified.json`；`data/build_unified.py` 組裝 records、provenance、trends、confidence 等，部分驅動模型與回測需預先產檔。圖為邏輯資料流，非聲稱每筆資料都逐站經過同一函式。 |
| unified → 網頁 → 瀏覽器 | `data/make_preview.py` 讀取資料並嵌入 `data/preview_template.html`，產生 `preview.html`。頁面不是每次開啟都向資料庫查詢；資料庫圖示僅表示 JSON 資料儲存，沒有 SQL 服務。 |
| 瀏覽器 → AI 入口 | `data/preview_template.html` 以 POST 發出 action 請求；`serve.py:105` 接 `/api`，呼叫共用 `deploy/lambda_handler.py` 的 `_ai()`。圖中 serve.py 與 Lambda 是可選執行方式，不表示串接成兩跳。 |
| AI 入口 → 模型介面 | `_ai()` 會讀取 unified 資料、交由 `llm/ask.py`、`llm/synthesize.py` 等組成有資料依據的提示；圖省略資料回讀線與回應反向線，避免主路徑重複，AI 並非沒有資料來源。 |
| 模型介面 → Bedrock | `llm/backend.py:76` 的 `BedrockBackend` 使用 `AnthropicBedrockMantle`；`load_backend()` 支援 stub、Anthropic、Bedrock。本機可由設定選擇，Lambda `_ai()` 未注入 backend 時選 Bedrock。本輪未讀取密鑰、未呼叫付費推論。 |

產品名稱採 YouthScope，程式保留的 YouthLens／YOUTHLENS 名称不自行更名。

## 部署與比賽合規限制

`deploy/deploy.sh` 含 S3 靜態網站、Lambda 與 **Lambda Function URL** 建立流程，不是 API Gateway 架構。腳本存在不代表已執行，也不證明帳號權限、模型存取、可公開展示的 URL 或端對端成功。

現有 S3 腳本關閉 Block Public Access 並設定公開讀取政策（`deploy/deploy.sh:43–50`），須按比賽附件要求修正；模型請求亦缺全域 ≤1 RPS 控制。這些不是本圖任務已修復的項目，不能以本圖充當合規交付證明。完整審查請見上層 `docs/COMPETITION_COMPLIANCE_REVIEW.md`（由主任務彙整）。

分工卡片為本次提案：Ray 負責需求痛點、構思、系統架構、應用整合、AWS 部署與 Bedrock 串接；Webb 負責資料、統計、預測及技術驗證。不是從 Git 歷史推定的既成貢獻認證。

## 重產

工具：<https://github.com/tt-a1i/archify>，Skill 2.17，commit `6db72a9aea3d0f67a6a034e41f8a5491476a11c1`。本輪工具放在 `/tmp/youthscope-archify-tool/archify`，沒有全域安裝。實作基準 HEAD：`2b1e276096e135c91ad6cfe725354b2a023f70ee`，另讀本機工作檔；未 merge remote。

取得相同工具 commit 後，從它的 `archify` 目錄執行（調整絕對路徑）：

```sh
node bin/archify.mjs validate architecture /Users/ayun/Documents/Project/youthspot/docs/architecture/youthscope.architecture.json --quality showcase --json
node bin/archify.mjs deliver architecture /Users/ayun/Documents/Project/youthspot/docs/architecture/youthscope.architecture.json /Users/ayun/Documents/Project/youthspot/docs/architecture/youthscope.html --quality showcase --json
node bin/archify.mjs visual-check /Users/ayun/Documents/Project/youthspot/docs/architecture/youthscope.html --json
```

SVG 為靜態抽取；重產時用 Python 擷取 HTML 第一個 `<svg>…</svg>`，把 HTML `<style>` 內容放進該 SVG 的 CDATA style 節點，確認 xmlns 為 `http://www.w3.org/2000/svg`，以 `xml.etree.ElementTree.fromstring()` 檢查。不得修改已冻结 HTML 來取得驗證通過。

## 驗證結論

- `deliver`：showcase 9／9；0 errors、0 warnings。
- `visual-check`：passed；1440×900、1600×1000、1920×1080、2048×1320 均無水平或垂直溢出，明暗端點截圖成功。
- 視覺審閱：已實際查看 1440 light 與 2048 dark PNG；文字、箭頭、卡片無遮擋，主路徑清楚。副標為次要資訊，小螢幕可使用縮放。
- SVG：XML 可解析，內容來自已截圖檢查的 inline SVG；沒有另外驗證獨立 SVG 在其他向量編輯器的呈現。
- 互動：未驗證。CUA 明確以 URL 安全政策拒絕 file URL，並禁止改用其他網址、瀏覽器介面或原始 CDP 等方式取得同一結果，因此未繞過。官方 visual-check 的既有證據只涵蓋上述尺寸、主題與渲染，不等於搜尋／聚焦／匯出按鈕可用。

規格 SHA-256：`41a6722deef8bc9005138718c74ed20c22768e8607195ca31b99114e528155e3`（4329 bytes）。HTML SHA-256：`192f51ce4858df43c59d3cd9399a76852d6be9ffbeedeb8a72ea85da83e8c9a3`（808753 bytes）。

## AWS 部署提案圖（第二張）

`aws-proposal.html`、`aws-proposal.architecture.json`、`aws-proposal.svg` 與 `aws-proposal.visual-check.*.png` 是供簡報審閱的**目標方案**，不是 GitHub／本機當前部署現況，也沒有套用程式變更。

瀏覽器透過 CloudFront 讀取私有 S3（OAC、Block Public Access）；AI 請求送入 Lambda，由待實作的共用速率閘門協調全部模型、全部實例與重試，使相鄰模型呼叫間隔大於 1 秒，再呼叫 Bedrock。這個閘門是邏輯元件，不表示已選用或部署獨立服務；須再決定原子協調、持久化與失敗處理，不能只用單程序 sleep。CloudWatch 監測、IAM、入口保護及模型權限尚待設定或驗證。提案部署區域限定 us-east-1 或 us-west-2，CloudFront 為全域服務。圖中私有 S3 用資料儲存分類表示物件儲存，不是資料庫產品。

所有新增或需變更的元件均標待新增／待修正／待實作／待驗證。Lambda 有現成 handler，但不推定它已部署或可直接公開；入口具體存取方案待定。現有公開 S3 腳本不可直接作為合規部署方案。

第二張重產時把上述三個 CLI 命令中的 `youthscope` 改為 `aws-proposal`。第二張 deliver 同樣 9／9、0 errors、0 warnings，官方 browser evidence passed，四個桌面尺寸皆無溢出；已目視 1440 light 與 2048 dark 截圖，箭頭、元件與狀態文字無遮擋。互動維持未驗證，不再次嘗試已被政策拒絕的存取方式。

第二張規格 SHA-256：`0489c103ce353beb5c101532ad4f8e02ac4e93c4b6ddd3fb5c2145bb1074efa8`（3003 bytes）。HTML SHA-256：`855fed0e239dbfe45c0cbcf0d0eee224b03be561e1064f9cb24c1e6a9e205eb7`（803065 bytes）。
