"""카카오톡 봇 API (developers.kakao.com/docs/ko/bot/webhook).

수신: POST {op:0, t:"MESSAGE_CREATE", d:{id, content, botGroupKey, botUserKey, callbackToken}} → 1초 안에 204
답장: POST /v1/bot/callback, Authorization: KakaoAK {봇 인증 토큰}, X-Bot-Callback-Token (5분 유효)
"""
import logging
import os

import httpx

log = logging.getLogger("kakao")
BOT_TOKEN = os.getenv("KAKAO_BOT_TOKEN", "")
CALLBACK_URL = "https://kapi.kakao.com/v1/bot/callback"
_http = httpx.Client(timeout=10)


def parse(payload: dict) -> dict | None:
    if payload.get("op") != 0:
        return None
    d = payload.get("d") or {}
    return {
        "type": payload.get("t"),
        "id": str(d.get("id", "")),
        "text": str(d.get("content") or ""),
        "chat": f"{d.get('botGroupKey', '')}:{d.get('botUserKey', '')}",
        "token": d.get("callbackToken") or "",
    }


def reply(callback_token: str, text: str) -> None:
    """SimpleText 말풍선 1개로 답장한다 (심사는 SimpleText만 평가)."""
    body = {"skillResponse": {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": text}}]}}}
    headers = {"Authorization": f"KakaoAK {BOT_TOKEN}", "X-Bot-Callback-Token": callback_token}
    for _ in range(2):
        try:
            r = _http.post(CALLBACK_URL, json=body, headers=headers)
            if r.status_code == 200:
                return
            log.error("callback %s %s", r.status_code, r.text[:300])
            if r.status_code < 500:
                return
        except httpx.HTTPError as e:
            log.error("callback error %s", e)
