# 교동마을 산지직송 CS 에이전트

카카오톡 봇 웹훅 → FastAPI → OpenAI 툴 호출 루프 → 운영진 액션 API → 카카오 콜백 답장.

| 파일 | 역할 |
|---|---|
| main.py | 웹훅 수신(1초 내 204), 백그라운드 처리, 슬립 방지 |
| agent.py | 케이스(TC)별 대화 상태, 사진/음성 입력, 툴 호출 루프, 답변 정리 |
| actions.py | openapi.json으로 액션 API 툴 40개 자동 생성 및 호출 |
| kakao.py | 카카오톡 봇 이벤트 파싱, 콜백 답장 |
| prompt.md | 응대 원칙, 답변 형식, 보안 규칙 |
| kb_*.md | 관계자 인터뷰와 운영 문서에서 정리한 운영 지식 |

환경변수: OPENAI_API_KEY, ACTION_TOKEN, KAKAO_BOT_TOKEN, (선택) LLM_MODEL, WEBHOOK_SECRET, TEST_KEY
