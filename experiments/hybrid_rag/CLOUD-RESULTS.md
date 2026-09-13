# YouthScope Managed KB 雲端實測 — 2026-09-13

## 結論

保留精確統計路徑，文件檢索作為增補。Managed KB雲端檢索已跑通，但尚不適合直接替換正式站問答。Gateway/Harness不是這個固定檢索流程的必要條件。

## 實際資源

- Region：us-west-2；KB：EOPWDZAJRT（ACTIVE）。
- Datasource：ZKTEGKQGWK；ingestion：ZL612RYYJD（COMPLETE）。
- 掃描2份文件及2份metadata，成功索引2份、失敗0份。
- S3：youthscope-rag-lab-629048311342-20260913；Block Public Access全開，物件AES256伺服器端加密。
- IAM：youthscope-rag-lab-kb-20260913；只讀該bucket，信任限制帳號及此KB ARN。
- 設定見cloud-lab.json；公開公告摘要與typed metadata見corpus/。
- 未建立Gateway或Harness；未改正式AWS服務、UI或主分支。
- 憑證只用於臨時記憶體程序，未保存到repo、OB或AWS profile。

## 5次真實Retrieve（0次生成）

| 問題 | 第一筆 | 判讀 |
| --- | --- | --- |
| 2026年35歲新北工作青年資格與次數 | 2026公告，0.3790 | 有18–40歲、工作地及三次諮詢依據 |
| 2024年諮詢機會數 | 2024公告，0.3994 | 有2024年度370個依據 |
| 履歷與面試服務 | 2026公告，0.3605 | 有服務項目依據 |
| 2026現在還剩370個名額？ | 2024公告，0.3313 | 不可用歷史配額推論目前餘額 |
| 三峽明年新增幾個YouBike站？ | 2024職涯公告，0.4202 | 完全無對應依據，不能回答站數 |

三題可回答案例的預期來源均排第一；樣本僅兩份人工摘要，不是正式召回率benchmark，也沒有生成答案或對照組。所有題目都回傳兩份文件。文件外問題分數反而最高，證明不可把分數當信心度、也不能用本次三題任意訂一個通用門檻。

實際回應見live-retrieval-results.json；問題及人工預期見evaluation-cases.json。來源、日期、public metadata均成功回傳，經prototype組裝成S統計/D文件。文件狀態retrieved只代表來源入口檢查通過；新增document_support_status=not_evaluated，不宣稱主張已驗證。

## 已知限制與下一步

1. 加入答案支持性判斷與拒答測試；對文件外問題、當前餘額等資料缺口必須拒絕編造。
2. 公告日期不等於政策有效期限，需要有效期間、版本與更新流程；不能簡單只保留最新文件而破壞歷史查詢。
3. 擴充真實文件與人工測試集，加入跨局權責、過期政策、數字/單位、prompt injection及中文檢索。回應內_language_code=en，需以較大中文語料另驗，不能據此宣稱語言最佳化。
4. 這次語料為人工整理的公開摘要，包含明示年份限制；不代表原始PDF解析或未整理語料有相同品質。
5. 完整生成、引用顯示、正式API和UI整合仍未做。須與固定模型/提示詞對照測試後再評估接正式站。
6. AWS實際可建立不等於競賽規範已核准；Managed KB服務/版本是否在比賽白名單仍須以競賽文件確認。

## 費用與保留

共5次標準Retrieve，依公告US$1/1000次，檢索部分約US$0.005；另有Managed index US$5/GB原始資料/月及S3儲存/請求。資料不到1MB。這是估算，不是帳單或硬性預算。未呼叫生成模型。测试KB與bucket目前保留，會持續有儲存費；未啟動定期同步。

官方：[Managed S3 metadata](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-s3.html)、[Retrieve](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Retrieve.html)、[價格](https://aws.amazon.com/bedrock/pricing/)。
