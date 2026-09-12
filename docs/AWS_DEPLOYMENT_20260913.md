> 歷史紀錄：以下反映當時審查或部署狀態。最新整合與操作方式見 [部署說明](../deploy/CLOUDFORMATION.md)；本輪已保留 Webber 最新功能並整合 YouthScope 品牌與私有 S3 部署。

# YouthScope AWS 部署紀錄

2026-09-13（Asia/Taipei）。依 Ray 授權，建立獨立展示環境，未變更舊 youthscope-demo。

- 展示網址：https://d3mmoubox2tlbx.cloudfront.net/
- API：https://d3mmoubox2tlbx.cloudfront.net/api
- Stack：youthscope-magnifier-20260913，UPDATE_COMPLETE
- Region：us-west-2
- CloudFront：E8KJKZHJV64CR，Deployed
- Lambda：youthscope-magnifier-20260913-api，Active／Successful；Python 3.12，60 秒 timeout
- Site bucket：youthscope-magnifier-20260913-sitebucket-mkgosfxlmjpp
- Artifact bucket：youthscope-magnifier-20260913-artifacts-629048311342
- Rate table：youthscope-magnifier-20260913-RateTable-AMPXS51CIZV5
- 模型：Amazon Bedrock amazon.nova-lite-v1:0。已驗證可呼叫；本次未開通 Claude 第三方協議。

## 已驗證

- 主頁 HTTPS 200；六都頁面在桌機 1440px 與手機 390px 實際切換成功，無 JS／console 錯誤或橫向溢出。
- Hero 為「YouthScope 青年放大鏡」，ys. Logo 使用橘紅色。
- S3 四項 Block Public Access 皆 true；policy IsPublic=false；匿名直接讀取 preview.html 為 403。CloudFront 使用 OAC 讀取。
- 公開 API health 正常，pipeline 操作回 400。正式入口 deploy.runtime.handler。
- 真實 Lambda → Bedrock 回應成功。回傳數字比對狀態只能表示數字命中記錄，不能視為所有敘述／因果推論正確；第一題人口現況回覆偏向勞動與薪資，仍需 Webb 審閱內容品質。
- 限流使用 owner lease 覆蓋整段模型呼叫，完成後冷卻 1100ms。三獨立 process 實際共用 DDB 驗證互斥；前次工作結束至下次開始分別 1592.944ms、1542.088ms。此測試為模擬工作，不額外呼叫模型；不保證其他 stack 或其他呼叫者的帳號總流量。
- 221 個本機測試成功，5 個略過；部署包 AST／ZIP／獨立 import 驗證成功。
- AWS Lambda CodeSha256 與發布 ZIP SHA256 一致：28b85a4195ce187ba7695c185262a854c0edea9b0b6480fdbe8fba28cfa8cbcd。
- Static ZIP SHA256：485dda66d21439e280349f7539248ac6a567a388a371c66c09f0fb3768f4012d。

## 範圍與維護

本次發布本機整合工作副本；GitHub 尚未 commit／push，因此不能宣稱遠端原始碼已包含部署修正。舊部署腳本已換成私有 S3 CloudFormation 入口，重部署需用新參數與不可變 artifact key。

此為公開展示 API，沒有使用者登入；直接 Lambda URL 也可呼叫，仍通過相同限流。沒有宣稱正式產品安全或整體競賽驗收通過。附圖與既有提案是歷史設計，實際元件以本紀錄與 AWS JSON 為準。

臨時 AWS 憑證只用於執行程序，未寫入 repository、部署包或 Obsidian。執行角色由 Lambda IAM role 提供，網站不需要本機臨時憑證。

證據：[AWS live](architecture/aws-live-deployment-20260913.json)、[發布 manifest](architecture/aws-release-manifest-20260913.json)。

## 網頁 AI 最終驗收

經公開 CloudFront 網頁送出一次 AI 問題：POST /api HTTP 200、模型 amazon.nova-lite-v1:0，回答成功顯示且無 JS 錯誤。數字檢查顯示 3 個命中來源；但文字把 25–29 歲數據泛稱青年、沒有完整回應資料限制，並輸出「空出一行」字樣。這是內容品質待辦，不能將數字命中視為語意驗證完成。建議 Webb 接續強化年齡範圍與引用／敘述檢核。

瀏覽器證據：architecture/aws-qa-20260913/，Obsidian 附件內亦已保存。
