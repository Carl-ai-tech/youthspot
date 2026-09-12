#!/usr/bin/env bash
# 安全部署入口：只使用私有 S3 + CloudFront OAC CloudFormation 範本。
# 預設僅列出命令；完整參數加 --execute 才會呼叫 AWS。
# 打包、上傳靜態檔與驗收請見 deploy/CLOUDFORMATION.md。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$ROOT/deploy/cloudformation.json"
STACK="" REGION="us-west-2" ARTIFACT_BUCKET="" ARTIFACT_KEY="" MODEL_ID="" FUNCTION_NAME="" PROFILE=""
MODEL_MODE="regional" RELEASE_COMMIT="unknown"
EXECUTE=false

usage() {
  cat <<'HELP'
使用方式（預設只顯示命令，不變更 AWS）：
  bash deploy/deploy.sh \
    --stack <new-stack-name> \
    --artifact-bucket <existing-private-bucket> \
    --artifact-key <releases/sha256/lambda.zip> \
    --model-id <model-or-supported-profile-id> \
    --function-name <unique-lambda-name> \
    [--region <us-west-2|us-east-1>] \
    [--model-mode <regional|us-sonnet-4-6>] \
    [--release-commit <git-commit>] \
    [--profile <aws-profile>] [--execute]

預設 API 區域 us-west-2、模型模式 regional、發布版本 unknown。
Sonnet 4.6 使用 --model-mode us-sonnet-4-6 --model-id us.anthropic.claude-sonnet-4-6。
此 profile 可路由到 us-east-1、us-east-2、us-west-2，並非單區推理。
--execute 使用已準備的 Lambda ZIP 建立／更新 CloudFormation stack。
不重打包、不上傳檔案、不設定 S3 公開存取，也不接受舊版環境變數預設。
請先閱讀 deploy/CLOUDFORMATION.md 並核對帳號、模型及發布包。
HELP
}

fail() { printf '錯誤：%s\n' "$*" >&2; usage >&2; exit 64; }
value_required() { [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 需要明確值"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack) value_required "$@"; STACK="$2"; shift 2 ;;
    --region) value_required "$@"; REGION="$2"; shift 2 ;;
    --artifact-bucket) value_required "$@"; ARTIFACT_BUCKET="$2"; shift 2 ;;
    --artifact-key) value_required "$@"; ARTIFACT_KEY="$2"; shift 2 ;;
    --model-id) value_required "$@"; MODEL_ID="$2"; shift 2 ;;
    --model-mode) value_required "$@"; MODEL_MODE="$2"; shift 2 ;;
    --release-commit) value_required "$@"; RELEASE_COMMIT="$2"; shift 2 ;;
    --function-name) value_required "$@"; FUNCTION_NAME="$2"; shift 2 ;;
    --profile) value_required "$@"; PROFILE="$2"; shift 2 ;;
    --execute) EXECUTE=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "不認識的參數：$1" ;;
  esac
done

[[ -n "$STACK" && -n "$ARTIFACT_BUCKET" && -n "$ARTIFACT_KEY" && -n "$MODEL_ID" && -n "$FUNCTION_NAME" ]] || fail '必須提供五個必要參數；舊版無參數部署已停用'
[[ "$REGION" == us-west-2 || "$REGION" == us-east-1 ]] || fail '區域只允許 us-west-2 或 us-east-1'
[[ "$STACK" =~ ^[A-Za-z][A-Za-z0-9-]{0,127}$ ]] || fail 'stack 名稱格式不正確'
[[ "$FUNCTION_NAME" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || fail 'Lambda 名稱格式不正確'
[[ "$MODEL_ID" =~ ^[a-z0-9][a-z0-9.:-]*$ ]] || fail '請提供 model 或支援的 profile ID，不使用 ARN'
[[ "$RELEASE_COMMIT" == unknown || "$RELEASE_COMMIT" =~ ^[0-9a-f]{7,40}$ ]] || fail 'release-commit 必須為 Git commit hash 或 unknown'
case "$MODEL_MODE" in
  regional)
    case "$MODEL_ID" in us.*|eu.*|apac.*|global.*) fail 'regional 模式不支援 inference profile' ;; esac ;;
  us-sonnet-4-6)
    [[ "$MODEL_ID" == us.anthropic.claude-sonnet-4-6 ]] || fail 'us-sonnet-4-6 模式只接受 us.anthropic.claude-sonnet-4-6' ;;
  *) fail 'model-mode 只支援 regional 或 us-sonnet-4-6' ;;
esac
[[ -f "$TEMPLATE" ]] || fail "缺少範本：$TEMPLATE"

AWS=(aws --region "$REGION" --no-cli-pager)
if [[ -n "$PROFILE" ]]; then AWS+=(--profile "$PROFILE"); fi
VALIDATE=("${AWS[@]}" cloudformation validate-template --template-body "file://$TEMPLATE")
DEPLOY=("${AWS[@]}" cloudformation deploy --template-file "$TEMPLATE"
  --stack-name "$STACK" --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset
  --parameter-overrides "ArtifactBucket=$ARTIFACT_BUCKET" "ArtifactKey=$ARTIFACT_KEY"
  "BedrockModelId=$MODEL_ID" "BedrockModelMode=$MODEL_MODE"
  "ReleaseCommit=$RELEASE_COMMIT" "FunctionName=$FUNCTION_NAME")
OUTPUTS=("${AWS[@]}" cloudformation describe-stacks --stack-name "$STACK"
  --query 'Stacks[0].Outputs' --output table)

if [[ "$EXECUTE" != true ]]; then
  printf '預覽模式，未呼叫 AWS。核對後加 --execute 執行：\n'
  printf '%q ' "${VALIDATE[@]}"; printf '\n'
  printf '%q ' "${DEPLOY[@]}"; printf '\n'
  printf '%q ' "${OUTPUTS[@]}"; printf '\n'
  exit 0
fi

command -v aws >/dev/null 2>&1 || fail '找不到 AWS CLI'
export AWS_PAGER=""
"${VALIDATE[@]}"
"${DEPLOY[@]}"
"${OUTPUTS[@]}"
printf '\nStack 步驟完成。請依 deploy/CLOUDFORMATION.md 上傳 static/ 並執行端對端驗收。\n'
