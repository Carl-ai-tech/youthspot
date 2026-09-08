"""AWS Lambda 進入點 —— 比賽當天用。

畫面是純靜態的（放 S3），需要伺服器的只有兩件事：

    POST /pipeline   按下按鈕，重新抓政府資料並更新 unified.json
    POST /ai         呼叫 Bedrock：讀掃描檔、回答問題、統整敘述

為什麼要有 Lambda 而不是讓瀏覽器直接打 Bedrock：
瀏覽器沒有 AWS 憑證，也不該有。把憑證留在伺服器端是唯一安全的做法。

部署方式見 deploy/README.md。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUCKET = os.environ.get("YOUTHLENS_BUCKET", "")
UNIFIED_KEY = os.environ.get("YOUTHLENS_UNIFIED_KEY", "unified.json")

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json; charset=utf-8",
}


def _reply(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": CORS,
            "body": json.dumps(body, ensure_ascii=False)}


def _load_unified() -> dict:
    """優先從 S3 讀（那是最新的），讀不到就退回打包在函式裡的那份。"""
    if BUCKET:
        try:
            import boto3
            obj = boto3.client("s3").get_object(Bucket=BUCKET, Key=UNIFIED_KEY)
            return json.loads(obj["Body"].read().decode("utf-8"))
        except Exception:                                  # noqa: BLE001
            pass
    return json.loads((ROOT / "data" / "unified.json").read_text(encoding="utf-8"))


def _run_pipeline() -> dict:
    """重新抓政府資料 → 對齊 → 寫回 S3。這就是畫面上那顆按鈕。"""
    from data import build_reference, build_unified

    ref = build_reference.build(refresh=True)
    build_reference.OUTPUT.write_text(
        json.dumps(ref, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = build_unified.build(refresh=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=1)

    if BUCKET:
        import boto3
        boto3.client("s3").put_object(
            Bucket=BUCKET, Key=UNIFIED_KEY, Body=blob.encode("utf-8"),
            ContentType="application/json; charset=utf-8", CacheControl="no-cache")

    conf: dict[str, int] = {}
    for r in payload["records"]:
        c = r["provenance"]["confidence"]
        conf[c] = conf.get(c, 0) + 1
    return {
        "ok": True,
        "records": len(payload["records"]),
        "confidence": conf,
        "sources": len(payload["meta"]["sources"]),
        "generated": payload["_generated"],
    }


def _ai(action: str, body: dict) -> dict:
    """三種 AI 工作。數字一律來自引擎，模型只負責看懂與表達。"""
    from llm.backend import load_backend
    backend = load_backend("bedrock")

    if action == "ask":
        from llm.ask import ask
        a = ask(body.get("question", ""), _load_unified(), backend)
        return {"ok": True, "answered": a.answered, "text": a.text,
                "records": a.records}

    if action == "synthesize":
        from llm.synthesize import synthesize
        g = synthesize(_load_unified(), backend,
                       region=body.get("region", "新北市"),
                       band=body.get("band", "18-35"),
                       question=body.get("question"))
        return {"ok": True, "text": g.text,
                "trustworthy": g.trustworthy,
                "verified": g.verified, "unverified": g.unverified,
                "summary": g.summary(), "records": g.records}

    if action == "scan":
        import base64
        import tempfile
        from llm.scan_table import read_table, to_source_records
        raw = base64.b64decode(body.get("image_base64", ""))
        suffix = body.get("suffix", ".png")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(raw)
            path = fh.name
        table = read_table(path, backend)
        return {
            "ok": True,
            "summary": table.summary(),
            "trustworthy": table.trustworthy,
            "issues": table.issues,
            "unreadable": table.unreadable,
            "rows": [{"age_label": r.age_label, "value": r.value,
                      "band": r.band.label if r.band else None} for r in table.rows],
            "source_records": len(to_source_records(table)) if table.usable_rows else 0,
        }

    raise ValueError(f"不認識的 action：{action}（可用：ask、synthesize、scan）")


def handler(event, context=None):
    """Lambda Function URL 的進入點。"""
    if (event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod")) == "OPTIONS":
        return {"statusCode": 204, "headers": CORS, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _reply(400, {"ok": False, "error": "body 不是合法 JSON"})

    action = body.get("action", "")
    try:
        if action == "pipeline":
            return _reply(200, _run_pipeline())
        return _reply(200, _ai(action, body))
    except Exception as exc:                               # noqa: BLE001
        # 失敗時把訊息傳回前端，不要只留在 CloudWatch —— demo 現場看不到 log
        return _reply(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    # 本機試跑：python deploy/lambda_handler.py '{"action":"pipeline"}'
    arg = sys.argv[1] if len(sys.argv) > 1 else '{"action":"pipeline"}'
    print(json.dumps(json.loads(handler({"body": arg})["body"]),
                     ensure_ascii=False, indent=2))
