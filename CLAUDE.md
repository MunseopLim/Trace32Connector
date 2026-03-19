# Trace32Connector

TRACE32 PowerView를 AI가 제어할 수 있게 하는 MCP 서버 / HTTP API / Python 라이브러리.

## 제약 조건 (반드시 준수)

- **Python 2.7 / 3.4 호환** 필수 — f-string 금지, `.format()` 또는 `%` 사용
- **외부 라이브러리 금지** — Python stdlib만 사용 (socket, struct, json, threading, BaseHTTPServer 등)
- **폐쇄망 환경** — 외부 네트워크 접속 불가, 모든 의존성은 프로젝트 내 포함

## 작업 규칙

- **커밋 금지** — 사용자의 명시적 허락 없이 절대 커밋하지 말 것
- **유닛 테스트 필수** — 모든 변경점은 관련 유닛 테스트를 수행하여 검증할 것
- **테스트 추가/수정** — 변경에 대한 유닛 테스트가 없으면 추가하고, 기존 테스트 수정이 필요하면 수정할 것
- **문서 업데이트** — 코드 변경 시 관련 문서(CLAUDE.md, README 등)도 함께 업데이트할 것

## 프로젝트 구조

```
t32/constants.py   — RCL 프로토콜 상수 (CMD, SUBCMD, STATE, ACCESS 등)
t32/client.py      — TCP 소켓 기반 TRACE32 클라이언트 (핵심)
mcp_server.py      — MCP stdio 서버 (JSON-RPC 2.0, 21개 tools)
http_server.py     — HTTP REST API 서버 (port 8032)
config.json        — 기본 설정 (host, port, timeout)
tests/             — 유닛 테스트 (unittest + mock TCP 서버)
```

## 테스트

```bash
python -m pytest tests/ -v --tb=short
```

Mock TCP 서버(`tests/test_client.py:MockTrace32Server`)를 사용하므로 실제 TRACE32 없이 테스트 가능.

## 프로토콜 구조

TCP 프레임: `[4바이트 LE 길이][메시지 본문]`
메시지 본문: `[CMD:1][SUBCMD:1][MSGID:1][페이로드:N]`

프로토콜 레퍼런스: TRACE32 설치 디렉토리 `~~/demo/api/capi/src/hremote.c`

## TRACE32 설정

config.t32에 추가:
```
RCL=NETTCP
PORT=20000
```
