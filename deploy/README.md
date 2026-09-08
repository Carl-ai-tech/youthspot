# 比賽當天（9/12）的部署步驟

AWS 環境只在 **9/12 08:00 – 9/13 13:00** 開放。以下全部在
**AWS CloudShell**（主控台右上角的終端機圖示）裡執行。

---

## 0. 進去先看一眼（30 秒）

Bedrock 主控台 → **Model access** → 確認 Anthropic 的模型是
「Access granted」。萬一沒開，申請要時間，越早發現越好。

## 1. 取得程式

```bash
git clone <你的 GitHub 網址> youthlens
cd youthlens
```

## 2. 確認 Bedrock 連得上

```bash
pip install anthropic boto3
export YOUTHLENS_LLM_BACKEND=bedrock
python verify_bedrock.py
```

八項逐一檢查。**模型 ID 那一項會列出這個帳號實際可用的模型** ——
如果我們預設的不在裡面，照它給的指令設定環境變數即可：

```bash
export YOUTHLENS_BEDROCK_MODEL=<清單裡的某一個>
```

## 3. 產生資料與畫面

```bash
python run_pipeline.py --refresh
```

約一分鐘（要重新抓政府資料）。跑完會有 `data/unified.json` 與 `preview.html`。

> 網路不順就拿掉 `--refresh`，用 repo 裡已經產好的資料。
> 那份是 9/8 抓的，數字一樣能 demo。

## 4. 部署

```bash
bash deploy/deploy.sh
```

會印出兩個網址：

- **Live Demo 網址** ← 這就是必繳的那一項，貼進提案表單
- **API 端點** ← 畫面上的按鈕與 AI 功能會打這裡

## 5. 驗證

```bash
curl -s -X POST <API 端點> -d '{"action":"pipeline"}'
```

回傳 `{"ok": true, "records": 4xx, ...}` 就代表 Lambda 真的跑完整條流程了。

---

## 架構

```
        瀏覽器
          │
          ├── 讀畫面與資料 ──────────→  S3（靜態網站託管）
          │                                 ↑
          │                                 │ 寫回更新後的 unified.json
          ├── [按鈕] 重跑 pipeline ──→  Lambda ──→ 政府開放資料 API
          │                                 │
          └── [問問題／讀掃描檔] ────→  Lambda ──→  Amazon Bedrock（Claude）
```

**為什麼 AI 要經過 Lambda 而不是瀏覽器直接打 Bedrock：**
瀏覽器沒有 AWS 憑證，也不該有。憑證留在伺服器端是唯一安全的做法。

**為什麼畫面是靜態的：**
資料量只有幾百 KB，全部嵌在網頁裡。沒有資料庫、沒有後端渲染 ——
即使 Lambda 掛了，畫面照樣打得開，只是按鈕跟 AI 功能不能用。
Demo 不會整個開天窗。

---

## 出事時

| 狀況 | 怎麼辦 |
|---|---|
| Bedrock 沒權限 | `verify_bedrock.py` 會指出來；先做 demo 的非 AI 部分 |
| 模型 ID 不存在 | 檢查腳本會列出可用的，改環境變數即可 |
| Lambda 逾時 | 已設 300 秒；仍逾時就拿掉 `--refresh`，用既有資料 |
| 部署失敗 | 本機雙擊 `preview.html` 一樣能完整 demo，只是沒有線上網址 |

**最後的保險：`preview.html` 是單一檔案、資料全部內嵌、不需要網路。**
所有雲端服務同時掛掉，這個檔案還是能打開。
