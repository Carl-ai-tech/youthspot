# YouthScope 私有 S3 發布包

`cloudformation.json` 是獨立部署範本；`package_release.py` 只在本機產生發布物，不連 AWS、不讀憑證、不安裝套件，也不修改來源 HTML。此說明不是上線或合規驗收證明。

## 範本參數與輸出

| 參數 | 用途 |
| --- | --- |
| `ArtifactBucket` | 已存在、與 Lambda 同區域的私有程式包 bucket；與本範本新建的 SiteBucket 分開。 |
| `ArtifactKey` | `lambda.zip` 的不可變物件 key，建議含 ZIP SHA-256。更新程式必須換 key。 |
| `BedrockModelId` | 精確模型 ID。Nova regional：`amazon.nova-lite-v1:0`；Sonnet US profile：`us.anthropic.claude-sonnet-4-6`。沒有模型預設值。 |
| `BedrockModelMode` | 預設 `regional`；切換 Sonnet 必須明確指定 `us-sonnet-4-6`。 |
| `ReleaseCommit` | 非敏感 Git commit hash，寫入 Lambda `RELEASE_COMMIT` 供 API health 回報；預設 `unknown`，正式發布應傳入實際來源 commit。 |
| `FunctionName` | 區域內唯一名稱；本次新 stack 使用 `youthscope-magnifier-20260913-api`，不要沿用舊 demo。 |

輸出：`SiteBucket`、`DistributionId`、`SiteUrl`、`ApiUrl`、`DirectFunctionUrl`、`FunctionName`、`RateTable`、`LogGroup`。

範本只接受 us-east-1／us-west-2 部署區域，部署腳本預設 API 區域為 us-west-2。`regional` 模式只授權來源區域的精確模型 ARN，保留 Nova 呼叫方式。`us-sonnet-4-6` 模式只接受 `us.anthropic.claude-sonnet-4-6`，授權當前帳號、來源區域的精確 profile ARN，以及 us-east-1、us-east-2、us-west-2 三區的 `anthropic.claude-sonnet-4-6` foundation-model ARN。三個 foundation-model 權限皆以 `bedrock:InferenceProfileArn` 限定該 profile；不授權直接 regional Sonnet 呼叫，沒有模型或區域 wildcard。

Sonnet 此模式是美國跨區推理：API 雖在 us-west-2，提示詞與結果可能在上述三區處理，不能宣稱單區。若比賽帳號或組織政策禁止其中某區，此 profile 可能被拒；本範本不變更 SCP、不接受模型協議或建立訂閱。profile 權限與 foundation-model 權限分開，避免把只在 foundation-model 評估時存在的 condition 加到 profile 本身。

## 實際路由與權限

- 靜態：瀏覽器 → CloudFront HTTPS → OAC／SigV4 → S3 regional REST origin。S3 四個 Block Public Access 都為 true、BucketOwnerEnforced、AES256；沒有 website endpoint，也不設公開讀取 ACL。Bucket policy 只允許本 distribution 的服務主體讀取，且拒絕非 TLS。
- 預設首頁為 `preview.html`；六都 `preview_城市.html` 原路徑保留。`/api` 與 `/api/*` 優先送 Lambda Function URL origin，API TTL 全為 0。轉發 headers（排除 viewer Host）、query string 與 POST body。靜態行為不允許 POST，所以 POST body 不會被當作 S3 寫入。
- CloudFront 允許 POST 的選項必須是七種 HTTP 方法組合；真正的 API method／action 白名單由 `deploy.runtime.handler` 實施。發布前必須驗證 GET health、拒絕非允許 action，以及 `pipeline` 無法執行。
- Function URL 為 `NONE` 並提供兩個資源權限：`InvokeFunctionUrl` 限 `FunctionUrlAuthType=NONE`；`InvokeFunction` 限 `InvokedViaFunctionUrl=true`。因此 origin 可被直接呼叫，CloudFront 不是 API 的身分驗證邊界。本版沒有 Lambda OAC、WAF 或使用者登入。
- Lambda Python 3.12、512 MB、60 秒，handler 為 `deploy.runtime.handler`。未設 reserved concurrency。DynamoDB PAY_PER_REQUEST 的 partition key 為字串 `pk`；所有此 stack 函式實例透過共用 table 節流。執行角色僅有該 table 的 UpdateItem、本站 bucket 的 GetObject、指定模型 InvokeModel、指定 7 天 retention log group 的 CreateLogStream／PutLogEvents，沒有 S3 PutObject。
- 環境包含 `YOUTHSCOPE_RATE_TABLE`、`YOUTHLENS_LLM_BACKEND=bedrock`、`YOUTHLENS_AWS_REGION`、`YOUTHLENS_BEDROCK_MODEL`、`YOUTHLENS_BUCKET`、`RELEASE_COMMIT`。Sonnet 的模型環境值維持 `us.anthropic.claude-sonnet-4-6`，供 Converse 直接選用 profile。

## 本機打包

先完成 `deploy/runtime.py` 與 `llm/rate_limit.py`。從專案根目錄執行：

```sh
python3 deploy/package_release.py --output /tmp/youthscope-release-20260913
```

輸出目錄必須不存在；拒絕覆寫。輸出包含 `lambda.zip`、`static.zip`、可供 S3 sync 的 `static/`，以及逐檔 SHA-256／bytes 的 `manifest.json`。固定 ZIP entry 時間與排序讓相同來源可重產相同 hash。

Lambda 僅收集 engine／llm／data 第一層 `.py`、明列的 deploy runtime／handler、`data/unified*.json`、`data/reference*.json`。保持 `deploy/`、`data/`、`llm/`、`engine/` 路徑，使 handler 的 ROOT 仍是 bundle 根目錄。AI 所需統計結果已嵌入 unified；不包 data cache、文件、憑證、測試真值、除錯資料或第三方套件。boto3／botocore 使用 AWS Python 3.12 runtime 提供的版本；沒有 pip install。

靜態包只包含六都 preview HTML、六都 unified JSON，以及頁面掃描範例所需的 `tests/fixtures/table32_sheet.png`。這些都是待發布的政府資料或展示素材。HTML 發布副本在 charset meta 後（亦支援合法 implicit head）注入 `window.YOUTHLENS_API="/api"`，利用既有受支援的 override；來源檔不改。

工具會解析所有打包 Python AST／JSON、檢查六都必要檔案、拒絕 symlink 或越界來源，並逐項讀回 ZIP 驗證內容與 CRC。這些本機檢查不代表模型連通或雲端路由驗證。

## 授權後的發布順序

1. 先核對新 stack、區域、模型與必要帳號權限。建立獨立私有 ArtifactBucket，四個 BPA 都開啟，以操作身份上傳 `lambda.zip`；ArtifactKey 使用 manifest 的 SHA。
2. 對 `deploy/cloudformation.json` 執行 AWS `validate-template`。本地 JSON 正確不取代 AWS 服務驗證。
3. 以 CAPABILITY_IAM 建立或更新新 stack，傳入 ArtifactBucket、ArtifactKey、BedrockModelId、BedrockModelMode、FunctionName、ReleaseCommit 六個參數。部署說明中的建議 stack 名是 `youthscope-magnifier-20260913`；不要改舊 demo。
4. Stack 完成後，以操作身份上傳 `static/` 到輸出 SiteBucket；不可加 public-read ACL，也不需關閉 BPA。使用 `aws s3 sync` 可依副檔名設定 Content-Type。HTML／JSON 建議 `Cache-Control: no-cache`，必要時 invalidation 清除舊版；發布失敗前不要先刪舊內容。
5. 驗證 CloudFront 首頁與六都 URL、`/api` health、POST JSON 轉發、拒絕 pipeline、S3 匿名物件讀取 403、Bedrock 回覆、節流及 CloudWatch 錯誤。驗證模型調用會消耗額度，需納入已授權的測試範圍。

刪除 stack 時 SiteBucket 保留，包含其物件；需另行授權才能清空／刪除。DynamoDB gate table 與 Lambda 隨 stack 移除，log group 為本版 7 天 retention。這個 stack 沒有處理其他 stack／帳號客戶端的推論頻率。

## 更新既有 stack 至 Sonnet 4.6

先用當前整合版本重新打包，將不可變 `lambda.zip` 上傳至同區域私有 artifact bucket。以下預設只列命令，核對後加 `--execute` 才執行；它使用 `cloudformation deploy` 建立 change set 並更新既有 stack。`--release-commit` 應是打包來源的完整 commit；工作目錄另有修改時不可只用舊 commit 當發布證據。

```sh
bash deploy/deploy.sh \
  --stack youthscope-magnifier-20260913 \
  --artifact-bucket '<existing-private-artifact-bucket>' \
  --artifact-key 'releases/<lambda-zip-sha256>/lambda.zip' \
  --function-name youthscope-magnifier-20260913-api \
  --model-mode us-sonnet-4-6 \
  --model-id us.anthropic.claude-sonnet-4-6 \
  --release-commit '<source-git-commit>'
```

若直接使用 `aws cloudformation update-stack`，保留原 ArtifactBucket、FunctionName，更新 ArtifactKey，並傳入 `BedrockModelMode=us-sonnet-4-6`、`BedrockModelId=us.anthropic.claude-sonnet-4-6`、`ReleaseCommit=<source-git-commit>` 及 `CAPABILITY_IAM`；不能只更新 Lambda 環境而略過 IAM。回到 Nova 時，明確傳入 `BedrockModelMode=regional` 和 `BedrockModelId=amazon.nova-lite-v1:0`，條件化政策會移除 profile 與三區 Sonnet 許可。

Stack 更新完成仍須上傳靜態包、完成 CloudFront invalidation，核對 `/api` 的模型及 release commit，並以新 Lambda 角色實際呼叫 AI。部署者身份可呼叫 Sonnet 不等於執行角色已有正確權限。

## 官方依據（2026-09-13 核對）

- [Bedrock Geographic cross-Region IAM](https://docs.aws.amazon.com/bedrock/latest/userguide/geographic-cross-region-inference.html)：profile 與全部目的區模型的精確權限，以及 foundation-model 上的 `bedrock:InferenceProfileArn` 條件。
- [Lambda Function URL 權限](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)：新 Function URL 需要兩種 invoke 許可。
- [CloudFormation Lambda Permission 屬性](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-lambda-permission.html)：`FunctionUrlAuthType`、`InvokedViaFunctionUrl`。
- [CloudFront 私有 S3 與 OAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)：OAC 使用一般 S3 origin、BucketOwnerEnforced 與限定 distribution 的 bucket policy。
- [CloudFront CacheBehavior](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-cloudfront-distribution-cachebehavior.html)：API 行為／方法設定。

## 初版打包驗證（後續部署以實際紀錄為準）

已建立 `/tmp/youthscope-release-20260913/`，並在另一新目錄重打一次；兩次完整 manifest 與兩個 ZIP hash 相同。Lambda 61 檔、static 13 檔。ZIP 解壓至隔離暫存目錄後，成功 import `deploy.runtime.handler`；ROOT 指向 bundle 根目錄，六都資料皆能由 `_load_city()` 讀取。六張 HTML 首行 charset 保留、override 恰好一次。模板 JSON 與關鍵權限／路由斷言通過；本子任務未向 AWS 執行 validate-template 或部署。

- lambda.zip：734797 bytes，SHA-256 `3ebf9f8d8016c1946875e64fd41e439d49d78c3009055809b5e2f4e5007f0f4f`
- static.zip：2179799 bytes，SHA-256 `485dda66d21439e280349f7539248ac6a567a388a371c66c09f0fb3768f4012d`

來源後續若再改動，必須以新目錄重新打包，使用新 manifest／ArtifactKey，不可沿用上述驗證宣稱。

## 部署版限流

正式程式使用 DynamoDB owner lease 包住完整模型呼叫，完成後再冷卻 1100ms；lease 180 秒，大於 Lambda 60 秒 timeout。逾時或未知錯誤不放行。只限制共用此表的新環境，無法約束既有 stack 或其他帳號呼叫者。最終 artifact hash 與 AWS 驗證請看 [實際部署紀錄](../docs/AWS_DEPLOYMENT_20260913.md)。
