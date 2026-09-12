> 歷史紀錄：以下反映當時審查或部署狀態。最新整合與操作方式見 [部署說明](../deploy/CLOUDFORMATION.md)；本輪已保留 Webber 最新功能並整合 YouthScope 品牌與私有 S3 部署。

# 競賽規範符合性審查

審查日期：2026-09-12（Asia/Taipei）。結論：**目前不可判定已符合全部競賽要求；部署腳本存在明確的 S3 公開存取違規設計，Bedrock 缺乏可保證請求上限的控制，交件與 AWS 實際狀態仍待驗證。** 本次僅審查並產生此文件，未改程式、GitHub 設定或部署。

## 範圍與證據版本

- GitHub：`Carl-ai-tech/youthspot`，`master` SHA `b934f14b3119c2a71ca30fc6c1b9b964c4dd701e`，審查時 repository 為 private。以下程式行號以此 GitHub 快照為準。
- 本機：`/Users/ayun/Documents/Project/youthspot`，HEAD `2b1e276096e135c91ad6cfe725354b2a023f70ee`，另有 README、提案書、工具說明、前端模板與輸出頁、snapshot 工具等未提交變更，以及未追蹤 `data/brand.svg`、`docs/INTEGRATION.md`。這些不代表 GitHub 已交付內容。
- 直接材料：`/Users/ayun/Downloads/黑客松競賽環境規範與限制_20260722.pdf`（2 頁）、`IMG_1413.HEIC`（9/13 繳交內容投影片，已轉 PNG 目視）、`Supported AWS Services List 20260722.xlsx`（Services List、EC2、SageMaker AI 工作表）。
- GitHub 快照與附件提取位於 `/tmp/youthscope-rules-review/`，僅為本次分析暫存。尚未查 AWS 帳戶、部署資源、模型授權、實際請求記錄及交件平台。

## 主要發現

### 1. 不符合：S3 部署腳本主動開啟公開存取

PDF p.1「一般性使用規範與限制」第 1 點禁止公開對外 S3 Bucket，要求透過 Block Public Access 或 Bucket Policy 限制公開存取。`deploy/deploy.sh:43–50` 將四項 Block Public Access 全部設為 false，並授予 `Principal: *` 對整桶物件 `s3:GetObject`；`:53、66–67` 使用公開 S3 website。這是可重現的程式設計衝突；**沒有證據表示已在 AWS 執行成功，不能寫成帳戶已違規。**

建議修正：保留 private S3 與全部 Block Public Access，公開展示經 CloudFront OAC 等授權來源送出。Services List 第 60 列包含 CloudFront 與 CreateOriginAccessControl/CreateDistribution，但仍須驗證競賽帳戶實際權限。此處只提案，未套用。

### 2. 控制缺失：Bedrock 無全域低於 1 RPS 保證

PDF p.1「Amazon Bedrock」第 1 點要求每秒 1 個請求以下。`llm/backend.py:100–121` 直接呼叫 `messages.create`，沒有節流；`deploy/lambda_handler.py:153–195` 每次工作可建後端並呼叫模型；`deploy/deploy.sh:124–130` 開啟匿名 Function URL，部署未設跨實例的限速機制。單一頁面按鈕或平均速度無法保證多使用者、多 Lambda 實例、工具腳本及 SDK 重試合計仍低於上限。

狀態：**程式控制不完整；實際超限未驗證。** 建議在所有 Bedrock 呼叫共用的入口排隊／分配全域時間槽，保守採請求開始時間間隔大於 1 秒，包含重試；以多客戶端並發與 AWS 端觀測驗證。僅 reserved concurrency=1 仍不能保證每秒請求數。

### 3. 部分符合：預設區域合規，但部署未一致指定區域

PDF p.1 一般第 6 點指定 `us-east-1`、`us-west-2` 為主要部署區域。`deploy/deploy.sh:16` 與 `llm/backend.py:27、86–98` 預設 us-east-1，符合預設設計。然而 REGION 可任意覆寫，且 Lambda CLI（`:106–133`）沒有 `--region "$REGION"`，也沒有匯出 AWS 預設區域；CLI profile 所在區域可能與 S3、Bedrock 設定分歧。

狀態：**AWS 實際部署區域未驗證。** 建議限制允許值並對區域型服務一致明示 region，讀回資源 ARN、S3 location 與 Bedrock region。

### 4. 部分符合：服務在白名單，模型與實際 IAM 未驗證

依 Services List 工作表：Bedrock 第 46 列；CloudFront 第 60 列；IAM 第 152 列；Lambda 第 176 列；CloudWatch Logs 第 181 列；S3 第 246 列；STS 第 288 列。腳本使用的核心服務及 CreateBucket、PutBucketPolicy、GetObject/PutObject、CreateFunction、CreateFunctionUrlConfig、IAM CreateRole/AttachRolePolicy/PutRolePolicy/PassRole、STS GetCallerIdentity、Bedrock InvokeModel 等有列入允許操作。

服務及 action 存在白名單不等於部署權限有效，也不使公開 S3 合法。沒有查競賽 IAM/SCP、權限邊界、模型可用性或 quota。`llm/backend.py:26、89–98` 預設 `anthropic.claude-opus-5` 與 `AnthropicBedrockMantle`；Excel 不提供該模型的授權證據，部署 `:105` 也沒有傳入特定 model ID。須核對實際 AWS 環境、SDK 版本及所需 API/IAM，不能僅因名稱含 Bedrock 認定已驗證。

目前未見 EC2/SageMaker 部署或大規模模型訓練程式，兩份執行個體清單對現有架構屬不適用；若後續改用，須逐一核對 EC2/SageMaker AI 工作表及現場最新限制。

### 5. 未驗證：禁用資料與輸入通道

PDF p.1 一般第 2 點禁止在 AWS 引入個資、受管制資料、財務資訊、健康等 13 類資料。既有資料多為政府公開彙總統計（`README.md:22–28`、`docs/提案書.md:51–60`），但公開不自動豁免規範。薪資、所得、貸款負擔等是否落入主辦方「財務資訊」範圍，需要以現場解釋確認，不能自行認定違規或豁免。

`deploy/lambda_handler.py:164–175、186–195、225–233` 接收自由問題、影像、表格文字；影像會在 AWS 暫存並傳模型，未见競賽禁用資料前置限制。未證實已輸入禁用內容。交件前應限制為已核准去識別／彙總 demo 資料，確認提示及實際輸入流程。

### 6. 部分符合：憑證防護與 Kiro 條件

`.gitignore` 有 `.env`、`*.env`、私鑰、`.aws/` 等排除，後端用環境變數／SDK 憑證提供者。對兩個工作樹文字檔執行常見 AWS Access Key、Anthropic token、GitHub token、private-key header 的有限模式掃描，未發現命中；未讀或輸出本機 `.env`。**這不等於完整密鑰稽核：未掃全部 Git 歷史，也不能檢出任意資料庫密碼或非標準 token。** 公開前仍需對將公開的完整歷史做不洩露值的 secret scan（PDF p.1 一般第 8 點）。

未見追蹤 `.kiro/`。PDF p.1 一般第 9 點只在使用 Kiro 時要求根目錄保留 specs/hooks/steering；未確認團隊是否使用 Kiro，故屬條件未驗證，不能直接判違規。

### 7. 未驗證：外部模型正式使用

`llm/backend.py:125–159、226–240` 保留 Anthropic 直連開發後端；Lambda `deploy/lambda_handler.py:160–162` 明確選 Bedrock，部署 `deploy/deploy.sh:105` 亦設定 bedrock。`serve.py:10–11、20` 的本機路徑則依環境選擇模型，`:43–45` 提及 Cloudflare Tunnel。

本次三份附件沒有明文規定所有模型必須来自 Bedrock/SageMaker；repo 自述 `llm/backend.py:8–9、136` 有此限制，但自述不能代替命題正式來源。若命題公告另有此規定，正式 demo 的 Anthropic 直連路徑即不可使用。現在只能確認 Lambda 的選擇意圖，不能認定實際 demo 已走 AWS 或已使用外部 AI 違規。

## 9/13 交件清單（圖片目視）

青年資料 AI 整合屬青年局命題。照片對此組要求以下六項，簡報另须涵蓋四項內容；地政局的額外勘查表不套用本案。照片未注明方案說明須 300 字或簡報必須 PDF，若交件平台另有格式限制需另行核對，不能把未見條文補成附件要求。附件的交件要求不構成本次公開 GitHub、正式部署、影片發布或上傳提交的授權。

| 要求 | 目前證據與判定 |
|---|---|
| 團隊基本資料 | repo 未見可確認已填寫並提交的表單；未驗證 |
| 解決方案說明 | `docs/提案書.md:13–40` 有痛點與方案；內容已有，提交未驗證 |
| 完整提案簡報檔案上傳 | 有 Markdown 提案書，未見追蹤 PPTX/PDF 最終簡報及平台上傳證據；未完成證據不足 |
| Live Demo 部署網址連結 | 有部署脚本、HTML 與本機伺服器，沒有本次查驗的正式 AWS demo URL；未驗證 |
| Live Demo 錄製影片連結 | 未見最終影片連結／上傳證據；未驗證 |
| GitHub 連結，含完整原始碼 | repo 存在且有程式，private；評審可讀性及是否含最後版本未驗證。本機變更未全部在 GitHub |
| 簡報：解決方案說明 | 提案書有對應素材，最終簡報未驗證 |
| 簡報：數據及資料運用 | `docs/提案書.md:51–67` 有素材，最終簡報未驗證 |
| 簡報：AWS 雲端技術架構（架構圖） | `docs/提案書.md:103–119` 是資料／邏輯架構，未具體畫 S3、Lambda、Bedrock 等 AWS 架構；需補進最終簡報 |
| 簡報：使用介面與操作流程 | `docs/提案書.md:85–91` 有 demo 腳本與 HTML 介面；最終簡報內容未驗證 |

**public repo 的判定界線：** 照片寫「GitHub 連結：含完整原始碼」，PDF 只規範上傳公開儲存庫時避免洩漏秘密；這三份材料沒有明文要求 repo 一律 public。因此 private 不是本次可確定的違規，應確認評審存取方式與更完整公告；不得擅自公開 repository。

## GitHub 與本機差異

本機 HEAD 與未提交工作樹相對 GitHub 有資料、回測、介面、文案等差異。本次並行工作若新增團隊／架構文件，只能算本機草稿，不回溯計為此 GitHub SHA 已包含或平台已提交。已對比 `deploy/`、`llm/`、`.gitignore`，這些主要合規路徑沒有差異，故前述 S3、節流、區域、模型切換風險同時存在。新增 `docs/INTEGRATION.md` 是整合紀錄，不是 AWS 部署或交件完成證明。不能把本機 UI 已整合寫成 GitHub 與競賽提交已同步。

## 建議處理順序與驗收

1. 在部署前修正公開 S3，並加入全域 Bedrock 速率控制與一致區域設定。
2. 在競賽帳戶唯讀核對實際 IAM、region、允許模型、模型授權範圍與資源數量；只開啟專案需要模型，保留定期檢閱／撤銷證據（PDF p.1 Bedrock 第 2、3 點）。
3. 確認財務彙總資料與任意輸入的適用範圍；以核准 demo 資料驗證 AWS 上實際操作。
4. 補齊 AWS 架構圖、最終簡報、AWS Live Demo 與影片連結，驗證評審能讀 GitHub 最後版本。
5. 核對正式公告與交件平台。PDF p.2 表明競賽實際環境／公告優先；本報告不是主辦方認證。

以上為審查建議，未執行修復、上傳、發佈或付費模型呼叫，也未把現有專案自述測試結果當成此次實測。
