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
t32/constants.py      — RCL 프로토콜 상수 (CMD, SUBCMD, STATE, ACCESS, MAX_CORES 등)
t32/client.py         — TCP 소켓 기반 TRACE32 클라이언트 (핵심)
t32/core_manager.py   — 멀티코어 매니저 + 엔디안 설정 + interpret_words()
mcp_server.py         — MCP stdio 서버 (JSON-RPC 2.0, 26개 tools, 멀티코어/엔디안)
http_server.py        — HTTP REST API 서버 (port 8032, 멀티코어/엔디안)
config.json           — 기본 설정 (host, port, timeout)
tests/                — 유닛 테스트 (unittest + mock TCP 서버)
```

## 멀티코어 지원

최대 16개 코어에 동시 접속 가능. 각 코어는 개별 TRACE32 PowerView 인스턴스(별도 포트)에 연결.

- `core_id` 파라미터 (0-15, 기본값 0) — 모든 tool/API에서 사용
- `t32_connect_all` — 연속 포트 범위로 일괄 접속
- `t32_list_cores` — 접속된 코어 목록 조회
- `t32_set_endian` / `t32_get_endian` — 코어별 엔디안 설정 (little/big)
- `t32_read_memory`의 `word_size` 옵션 — 엔디안 기반 워드 해석 (16/32비트)
- 하위호환: `core_id` 생략 시 core 0 사용 (기존 단일코어 동작 동일)

```bash
# MCP: 개별 코어 접속
{"name": "t32_connect", "arguments": {"host": "10.0.0.5", "port": 20003, "core_id": 3}}

# HTTP: 일괄 접속
curl -X POST http://localhost:8032/api/connect_all \
     -d '{"host": "10.0.0.5", "base_port": 20000, "num_cores": 16}'

# HTTP: 특정 코어 레지스터 읽기
curl -X POST http://localhost:8032/api/register/read \
     -d '{"name": "PC", "core_id": 5}'
```

## 테스트

```bash
python -m pytest tests/ -v --tb=short
# 또는
python -m unittest discover -s . -p '*test*.py' -v
```

Mock TCP 서버(`tests/test_client.py:MockTrace32Server`)를 사용하므로 실제 TRACE32 없이 테스트 가능.
16코어 시뮬레이션 테스트 포함 (`tests/test_core_manager.py:TestCoreManagerSixteenCores`).

## 프로토콜 구조

TCP 프레임: `[4바이트 LE 길이][메시지 본문]`
메시지 본문: `[CMD:1][SUBCMD:1][MSGID:1][페이로드:N]`

프로토콜 레퍼런스: TRACE32 설치 디렉토리 `~~/demo/api/capi/src/hremote.c`

## TRACE32 설정

config.t32에 추가 (코어당 별도 인스턴스):
```
RCL=NETTCP
PORT=20000
```

멀티코어 환경에서는 각 코어별 PowerView가 연속 포트를 사용:
- Core 0: PORT=20000
- Core 1: PORT=20001
- ...
- Core 15: PORT=20015
