"""상담 에이전트: 대화 상태 관리 + OpenAI 툴 호출 루프."""
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

import actions

log = logging.getLogger("agent")

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL = os.getenv("LLM_MODEL", "gpt-4.1")
MAX_STEPS = 8
MAX_CHARS = 480  # 심사 기준: 말풍선당 500자 초과 시 제외

ROOT = Path(__file__).parent
SYSTEM = (ROOT / "prompt.md").read_text(encoding="utf-8") + "\n\n# 운영 지식\n" + "\n\n".join(
    p.read_text(encoding="utf-8") for p in sorted(ROOT.glob("kb_*.md")))

_http = httpx.Client(timeout=60)
_tool_pool = ThreadPoolExecutor(max_workers=8)
_sessions: dict[str, dict] = {}
_locks: dict[str, threading.Lock] = {}

TC_RE = re.compile(r"(?<![A-Za-z_])(?:TC\s*ID|TC|id)\s*[:：=]\s*([A-Za-z0-9][A-Za-z0-9._-]{0,99})", re.I)
TC_ONLY_RE = re.compile(r"^\s*\[?([A-Z]{1,3}-\d{1,3}(?:-[A-Za-z0-9]+)?)\]?\s*$")
MEDIA_RE = re.compile(r"(image|audio)\s*:\s*(https?://\S+)", re.I)
CLAIM_RE = re.compile(r"ESC-\d|넘겨 두었|접수했|접수되었|접수해 두|접수 완료|연결해 두|이관했|이관해 두|전달해 두")
NUDGE = ("[시스템 확인] 방금 답변은 접수나 이관을 했다고 말했지만 이번 턴에 접수 API를 호출하지 않았다. "
         "필요하면 지금 해당 API를 호출해 실제 반환된 접수번호로 답하고, 호출하지 않을 거면 접수했다는 표현 없이 다시 답하라.")
SECRETS = [s for s in (OPENAI_KEY, actions.TOKEN, os.getenv("KAKAO_BOT_TOKEN", "")) if len(s) > 8]


def _openai(path: str, **kw) -> httpx.Response:
    r = _http.post(f"https://api.openai.com/v1/{path}", headers={"Authorization": f"Bearer {OPENAI_KEY}"}, **kw)
    r.raise_for_status()
    return r


def _transcribe(url: str) -> str:
    audio = _http.get(url).content
    name = url.rsplit("/", 1)[-1] or "audio.m4a"
    return _openai("audio/transcriptions", data={"model": "gpt-4o-mini-transcribe", "language": "ko"},
                   files={"file": (name, audio)}).json().get("text", "")


def _user_content(text: str):
    """image:/audio: URL 을 멀티모달 입력으로 바꾼다."""
    images, parts = [], []
    for kind, url in MEDIA_RE.findall(text):
        if kind.lower() == "image":
            images.append(url)
        else:
            try:
                parts.append(f"[고객 음성 메시지 전사] {_transcribe(url)}")
            except Exception as e:
                log.error("transcribe failed %s", e)
                parts.append("[고객 음성 메시지: 전사 실패]")
    body = MEDIA_RE.sub(lambda m: f"[{'사진' if m.group(1).lower() == 'image' else '음성'} 첨부]", text).strip()
    content_text = "\n".join([body] + parts)
    if not images:
        return content_text
    return [{"type": "text", "text": content_text}] + [{"type": "image_url", "image_url": {"url": u}} for u in images]


def _clean(text: str) -> str:
    text = re.sub(r"[*#`]+", "", text or "").strip()
    for s in SECRETS:
        text = text.replace(s, "")
    if len(text) > MAX_CHARS:
        cut = text.rfind(".", 0, MAX_CHARS)
        text = text[: cut + 1 if cut > MAX_CHARS // 2 else MAX_CHARS]
    return text or "확인 후 다시 안내드리겠습니다."


def _run(session: dict) -> str:
    msgs = [{"role": "system", "content": SYSTEM}] + session["msgs"]
    posted, nudged = False, False
    for _ in range(MAX_STEPS):
        msg = _openai("chat/completions", json={
            "model": MODEL, "messages": msgs, "tools": actions.TOOLS, "temperature": 0.2,
        }).json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        msgs.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if not calls:
            content = msg.get("content") or ""
            if not posted and not nudged and CLAIM_RE.search(content):
                nudged = True  # 접수 API 호출 없이 접수했다고 말한 경우 한 번 바로잡는다
                msgs.append({"role": "user", "content": NUDGE})
                continue
            return content
        posted |= any(actions.ROUTES.get(c["function"]["name"], ("GET",))[0] != "GET" for c in calls)

        def run_call(c):
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            return actions.call(c["function"]["name"], args, session["tc"])

        for c, out in zip(calls, _tool_pool.map(run_call, calls)):
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out})
    return "확인에 시간이 걸리고 있어요. 담당자 확인 후 다시 안내드리겠습니다."


def respond(chat: str, text: str) -> str:
    lock = _locks.setdefault(chat, threading.Lock())
    with lock:
        session = _sessions.setdefault(chat, {"tc": None, "msgs": []})
        m = TC_RE.search(text) or TC_ONLY_RE.match(text)
        if m and m.group(1) != session["tc"]:
            session.update(tc=m.group(1), msgs=[])  # 새 케이스: 대화 초기화
            log.info("new case tc=%s", session["tc"])
        session["msgs"].append({"role": "user", "content": _user_content(text)})
        try:
            answer = _clean(_run(session))
        except Exception as e:
            log.exception("agent error %s", e)
            session["msgs"].pop()
            return "죄송해요, 잠시 문제가 생겼어요. 한 번만 다시 말씀해 주시겠어요?"
        session["msgs"].append({"role": "assistant", "content": answer})
        session["msgs"] = session["msgs"][-40:]
        return answer
