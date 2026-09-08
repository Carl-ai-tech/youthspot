#!/usr/bin/env bash
# 一鍵部署到 AWS。9/12 在 AWS CloudShell 或 Cloud9 裡執行。
#
#   bash deploy/deploy.sh
#
# 做四件事：
#   1. 建 S3 儲存桶，開靜態網站託管
#   2. 上傳 preview.html 與 unified.json      → 這就是必繳的「Live Demo 部署網址」
#   3. 打包並建立 Lambda（跑 pipeline、呼叫 Bedrock）
#   4. 開 Function URL，把網址寫回網頁
#
# 可重複執行：已經存在的資源會更新而不是報錯。

set -euo pipefail

REGION="${YOUTHLENS_AWS_REGION:-us-east-1}"
STACK="${YOUTHLENS_STACK:-youthlens}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${STACK}-${ACCOUNT}"
FUNC="${STACK}-api"
ROLE="${STACK}-lambda-role"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()  { printf '  ✅ %s\n' "$*"; }

cd "$ROOT"

# ─────────────────────────────────────────────── 1. 靜態網站
say "1/4　建立 S3 儲存桶並開啟靜態網站託管"
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  ok "儲存桶已存在：$BUCKET"
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
  ok "已建立 $BUCKET"
fi

# 靜態網站要能被公開讀取，所以關掉封鎖公開存取
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=false,IgnorePublicAcls=false,BlockPublicPolicy=false,RestrictPublicBuckets=false" \
  >/dev/null
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$(cat <<POLICY
{"Version":"2012-10-17","Statement":[{"Sid":"PublicRead","Effect":"Allow",
"Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::$BUCKET/*"}]}
POLICY
)" >/dev/null
aws s3 website "s3://$BUCKET" --index-document index.html >/dev/null
ok "靜態網站託管已開啟"

# ─────────────────────────────────────────────── 2. 上傳畫面
say "2/4　上傳畫面與資料"
if [ ! -f preview.html ]; then
  echo "  找不到 preview.html，先在本機跑： python run_pipeline.py" >&2
  exit 1
fi
aws s3 cp preview.html "s3://$BUCKET/index.html" \
  --content-type "text/html; charset=utf-8" --cache-control "no-cache" >/dev/null
aws s3 cp data/unified.json "s3://$BUCKET/unified.json" \
  --content-type "application/json; charset=utf-8" --cache-control "no-cache" >/dev/null
SITE="http://$BUCKET.s3-website-$REGION.amazonaws.com"
[ "$REGION" = "us-east-1" ] || SITE="http://$BUCKET.s3-website.$REGION.amazonaws.com"
ok "$SITE"

# ─────────────────────────────────────────────── 3. Lambda
say "3/4　建立 Lambda（pipeline ＋ Bedrock）"

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
      "Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    >/dev/null
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null
  aws iam put-role-policy --role-name "$ROLE" --policy-name youthlens \
    --policy-document "$(cat <<POLICY
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],
  "Resource":"arn:aws:s3:::$BUCKET/*"},
 {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream",
  "bedrock:ListFoundationModels"],"Resource":"*"}]}
POLICY
)" >/dev/null
  ok "已建立角色 $ROLE（等 IAM 生效 10 秒）"
  sleep 10
else
  ok "角色已存在"
fi
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)"

BUILD="$(mktemp -d)"
cp -r engine data llm deploy "$BUILD/"
rm -rf "$BUILD/data/_cache" "$BUILD"/*/__pycache__ 2>/dev/null || true
cp "$BUILD/deploy/lambda_handler.py" "$BUILD/lambda_handler.py"
python3 -m pip install --quiet --target "$BUILD" anthropic 2>/dev/null \
  || pip install --quiet --target "$BUILD" anthropic
(cd "$BUILD" && zip -qr /tmp/youthlens.zip .)
ok "打包完成（$(du -h /tmp/youthlens.zip | cut -f1)）"

ENVVARS="Variables={YOUTHLENS_BUCKET=$BUCKET,YOUTHLENS_LLM_BACKEND=bedrock,YOUTHLENS_AWS_REGION=$REGION}"
if aws lambda get-function --function-name "$FUNC" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNC" \
    --zip-file fileb:///tmp/youthlens.zip >/dev/null
  aws lambda wait function-updated --function-name "$FUNC"
  aws lambda update-function-configuration --function-name "$FUNC" \
    --timeout 300 --memory-size 1024 --environment "$ENVVARS" >/dev/null
  ok "已更新既有函式"
else
  aws lambda create-function --function-name "$FUNC" \
    --runtime python3.12 --handler lambda_handler.handler --role "$ROLE_ARN" \
    --zip-file fileb:///tmp/youthlens.zip --timeout 300 --memory-size 1024 \
    --environment "$ENVVARS" >/dev/null
  aws lambda wait function-active --function-name "$FUNC"
  ok "已建立 $FUNC"
fi

# ─────────────────────────────────────────────── 4. Function URL
say "4/4　開啟 Function URL"
if ! aws lambda get-function-url-config --function-name "$FUNC" >/dev/null 2>&1; then
  aws lambda create-function-url-config --function-name "$FUNC" \
    --auth-type NONE --cors '{"AllowOrigins":["*"],"AllowMethods":["POST"],"AllowHeaders":["content-type"]}' \
    >/dev/null
  aws lambda add-permission --function-name "$FUNC" --statement-id public \
    --action lambda:InvokeFunctionUrl --principal '*' \
    --function-url-auth-type NONE >/dev/null
fi
API="$(aws lambda get-function-url-config --function-name "$FUNC" \
  --query FunctionUrl --output text)"
ok "$API"

printf '\n%s\n' "────────────────────────────────────────────────────────"
printf '  Live Demo 網址（必繳項目）\n    %s\n\n' "$SITE"
printf '  API 端點\n    %s\n\n' "$API"
printf '  驗證：\n'
printf '    curl -s -X POST %s -d %s\n' "$API" "'{\"action\":\"pipeline\"}'"
printf '%s\n\n' "────────────────────────────────────────────────────────"
