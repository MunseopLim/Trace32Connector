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
- **커밋 전 문서 체크리스트** — 사용자가 커밋을 요청하면, 커밋 실행 전에 반드시 아래 항목을 확인하고 업데이트할 것:
  1. `CLAUDE.md` — 프로젝트 구조, 테스트 수, 프로토콜/기능 설명이 현재 코드와 일치하는지
  2. `README.md` — 도구 수/목록, 테스트 수, 사용 예시가 현재 코드와 일치하는지
  3. 새 파일 추가 시 프로젝트 구조 섹션에 반영했는지

## 프로젝트 구조

```
t32/constants.py      — RCL 프로토콜 상수 (CMD, SUBCMD, STATE, ACCESS, NETASSIST 등)
t32/client.py         — UDP 소켓 기반 TRACE32 클라이언트 (NETASSIST 프로토콜, 스레드 안전)
t32/core_manager.py   — 멀티코어 매니저 + 엔디안 설정 + keepalive 스레드
mcp_server.py         — MCP stdio 서버 (JSON-RPC 2.0, 26개 tools, prompts/resources, 멀티코어/엔디안)
http_server.py        — HTTP REST API 서버 (port 8032, 멀티코어/엔디안)
config.json           — 기본 설정 (host, port, timeout)
diag_connect.py       — NETASSIST 프로토콜 진단 스크립트 (실제 T32 디버깅용)
tests/                — 유닛 테스트 (unittest + mock UDP 서버, 234개)
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
# Python 3.5+
python -m pytest tests/ -v --tb=short

# Python 2.7 / 3.4 (pytest 5.0+는 3.4 미지원)
python -m unittest discover -s tests -p "test_*.py" -v
```

Mock UDP 서버(`tests/test_client.py:MockTrace32Server`)를 사용하므로 실제 TRACE32 없이 테스트 가능.
16코어 시뮬레이션 테스트 포함 (`tests/test_core_manager.py:TestCoreManagerSixteenCores`).

## 프로토콜 구조 (NETASSIST/UDP)

UDP 패킷: `[타입:1][플래그:1][시퀀스:2][데이터]`
메시지 본문: `[LEN:1][CMD:1][SUBCMD:1][MSGID:1][페이로드:N]`

연결 흐름: UDP 핸드셰이크 → 3-way Sync (SYNCREQUEST/SYNCACKN/SYNCBACK) → ATTACH

프로토콜 레퍼런스: TRACE32 설치 디렉토리 `~~/demo/api/capi/src/hremote.c`, `hlinknet.c`

**참고**: NETTCP(TCP)는 PowerDebug X50에서만 지원. PowerDebug II/III는 NETASSIST(UDP)만 가능.

## Keepalive / 스레드 안전

- CoreManager가 30초 간격 keepalive 데몬 스레드를 자동 관리 (connect 시 시작, disconnect_all 시 중지)
- `Trace32Client._exchange()`: `threading.Lock()`으로 transmit+receive를 원자적 보호
- keepalive ping과 MCP 명령이 동시 실행되어도 프로토콜 충돌 없음

## 진단

실제 T32 접속 문제 디버깅 시 `diag_connect.py` 사용:
```bash
python diag_connect.py <host> <port>
# 각 단계(Connection, Sync, Attach, Ping, Cmd)의 raw 바이트 출력
```

## TRACE32 설정

config.t32에 추가 (코어당 별도 인스턴스):
```
RCL=NETASSIST
PORT=20000
PACKLEN=1024
```

멀티코어 환경에서는 각 코어별 PowerView가 연속 포트를 사용:
- Core 0: PORT=20000
- Core 1: PORT=20001
- ...
- Core 15: PORT=20015
