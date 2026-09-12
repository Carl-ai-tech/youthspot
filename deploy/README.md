# YouthScope AWS 部署

目前採用 CloudFront + 私有 S3 + Lambda + DynamoDB 限流 + Amazon Bedrock。

請依 [CLOUDFORMATION.md](CLOUDFORMATION.md) 打包、建立 stack、上傳靜態檔並驗收。`deploy.sh` 是安全的 CloudFormation 入口，必須明確提供參數；`--help` 查看用法。

[2026-09-13 實際部署紀錄](../docs/AWS_DEPLOYMENT_20260913.md) 保存本次網址、資源及驗證結果。

舊版 S3 公開網站流程已停用。臨時 AWS 憑證只透過執行環境提供，不寫入程式或發布包。公開 handler 為 `deploy.runtime.handler`，不允許資料重建 pipeline。
