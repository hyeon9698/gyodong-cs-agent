"""교동마을 산지직송 CS 에이전트 서버.

POST /webhook  카카오톡 봇 웹훅 (즉시 204, 백그라운드 처리 후 콜백 API로 답장)
POST /chat     자체 검증용 (TEST_KEY 설정 시에만)
GET  /health   헬스체크 겸 슬립 방지 대상
"""
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import Body, FastAPI, Header, Request, Response

import agent
import kakao

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")

app = FastAPI()
_pool = ThreadPoolExecutor(max_workers=32)
_seen: deque[str] = deque(maxlen=5000)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # 웹훅 커스텀 헤더 X-Webhook-Secret 값
WELCOME = "안녕하세요, 교동마을 산지직송입니다. 무엇을 도와드릴까요?"


def _handle(ev: dict) -> None:
    if ev["type"] == "MESSAGE_CREATE":
        kakao.reply(ev["token"], agent.respond(ev["chat"], ev["text"]))
    elif ev["type"] in ("ENTRANCE", "CHAT_JOIN") and ev["token"]:
        kakao.reply(ev["token"], WELCOME)


@app.post("/webhook")
async def webhook(req: Request) -> Response:
    if WEBHOOK_SECRET and req.headers.get("x-webhook-secret") != WEBHOOK_SECRET:
        return Response(status_code=204)
    try:
        ev = kakao.parse(await req.json())
    except ValueError:
        ev = None
    if ev and ev["id"] not in _seen:
        _seen.append(ev["id"])
        log.info("in %s chat=%s text=%s", ev["type"], ev["chat"][-6:], ev["text"][:120])
        _pool.submit(_handle, ev)
    return Response(status_code=204)


@app.post("/chat")
def chat(body: dict = Body(...), x_test_key: str = Header("")) -> dict:
    """배포 후 자체 검증용. TEST_KEY 환경변수가 없으면 비활성."""
    key = os.getenv("TEST_KEY")
    if not key or x_test_key != key:
        return {"error": "disabled"}
    return {"reply": agent.respond(f"test:{body.get('chat', 'default')}", body.get("text", ""))}


@app.get("/health")
def health() -> dict:
    return {"ok": True}


def _keep_awake() -> None:
    """무료 호스팅이 잠들지 않도록 4분마다 자기 공개 주소를 호출한다."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    while url:
        time.sleep(240)
        try:
            httpx.get(f"{url}/health", timeout=10)
        except httpx.HTTPError:
            pass


threading.Thread(target=_keep_awake, daemon=True).start()
