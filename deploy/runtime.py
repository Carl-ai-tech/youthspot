"""Read-only public demo API; administrative pipeline is never internet callable."""
from __future__ import annotations

import base64
import json
import os

from deploy.lambda_handler import _ai
from llm.rate_limit import request_budget

ALLOWED_ACTIONS = frozenset({'ask', 'advise', 'synthesize', 'scan', 'scan_text'})
MAX_BODY_BYTES = 4 * 1024 * 1024


def reply(status, payload):
    return {'statusCode': status, 'headers': {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    }, 'body': json.dumps(payload, ensure_ascii=False)}


def handler(event, context=None):
    method = event.get('requestContext', {}).get('http', {}).get('method') or event.get('httpMethod', 'POST')
    path = event.get('rawPath', '/api')
    if path not in ('/', '/api', '/api/health'):
        return reply(404, {'ok': False, 'error': '找不到此API路徑'})
    if method == 'OPTIONS':
        return reply(204, {})
    if method == 'GET':
        return reply(200, {'ok': True, 'service': 'YouthScope 青年放大鏡',
                          'release_commit': os.getenv('RELEASE_COMMIT', 'unknown'),
                          'backend': 'bedrock', 'model': os.getenv('YOUTHLENS_BEDROCK_MODEL', ''),
                          'region': os.getenv('YOUTHLENS_AWS_REGION', ''),
                          'rate_limit_configured': bool(os.getenv('YOUTHSCOPE_RATE_TABLE')),
                          'read_only': True})
    if method != 'POST':
        return reply(405, {'ok': False, 'error': '此路徑僅接受GET或POST'})
    try:
        raw = event.get('body') or ''
        if not isinstance(raw, str) or len(raw) > MAX_BODY_BYTES * 2:
            return reply(413, {'ok': False, 'error': '輸入資料過大'})
        raw = base64.b64decode(raw, validate=True) if event.get('isBase64Encoded') else raw.encode('utf-8')
        if len(raw) > MAX_BODY_BYTES:
            return reply(413, {'ok': False, 'error': '輸入資料過大'})
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError('JSON必須為物件')
        action = body.get('action')
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            return reply(400, {'ok': False, 'error': '不支援此操作；公開展示不提供資料重建功能'})
        for field, limit in [('question', 4000), ('region', 80), ('band', 40), ('text', 100000)]:
            if field in body and (not isinstance(body[field], str) or len(body[field]) > limit):
                raise ValueError(f'{field}格式或長度不正確')
        if action == 'scan':
            if body.get('suffix', '.png') not in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                raise ValueError('圖片格式不支援')
            if not isinstance(body.get('image_base64'), str) or not base64.b64decode(body['image_base64'], validate=True):
                raise ValueError('圖片資料不正確')
    except (ValueError, TypeError, UnicodeError):
        return reply(400, {'ok': False, 'error': '輸入不是有效的JSON或欄位格式不正確'})
    try:
        seconds = min(55, context.get_remaining_time_in_millis() / 1000 - 5) if context else 55
        with request_budget(seconds):
            result = _ai(action, body)
        result.setdefault('model', os.getenv('YOUTHLENS_BEDROCK_MODEL', ''))
        return reply(200, result)
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code', type(exc).__name__)
        print(json.dumps({'event': 'api_failure', 'action': action, 'error_code': code}))
        if code == 'InferenceTimeout':
            return reply(504, {'ok': False, 'error': 'AI回應逾時，請稍後再試；為避免重複推論，服務可能暫停接收AI請求最多3分鐘，頁面資料仍可查閱', 'code': code})
        if code in ('ThrottlingException', 'TooManyRequestsException', 'RateLimitTimeout', 'RateLimitError'):
            return reply(429, {'ok': False, 'error': '目前使用人數較多，請稍後重試', 'code': code})
        return reply(503, {'ok': False, 'error': 'AI服務暫時無法完成，請稍後重試；頁面資料仍可查閱', 'code': code})
