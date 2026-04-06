# Trace32Connector

폐쇄망 환경에서 AI가 Lauterbach TRACE32 디버거를 원격 제어할 수 있는 도구 모음.

- **MCP Server** — Claude Code, Cursor 등 MCP 지원 AI 에이전트용
- **HTTP REST API** — curl, 스크립트, 웹 기반 AI용
- **Python Library** — 직접 스크립트 작성용

## 요구사항

- Python 2.7 이상 (외부 라이브러리 불필요)
- TRACE32 PowerView (09/2020 이상, RCL=NETASSIST 지원)

## 빠른 시작

### 1. TRACE32 PowerView 설정

`config.t32` 파일에 아래 내용을 추가하고 PowerView를 재시작:

```
RCL=NETASSIST
PORT=20000
PACKLEN=1024
```

### 2-A. MCP Server (AI 에이전트 연동)

```bash
python mcp_server.py
```

Claude Code 등에서 MCP 서버로 등록하면 36개 디버깅 도구를 AI가 직접 사용할 수 있습니다.
접속 후 30초 간격으로 keepalive ping이 자동 동작하여 idle 시에도 연결이 유지됩니다.

**MCP 프로토콜 지원 기능:**
- Tool annotations (readOnlyHint, destructiveHint) — AI가 tool 위험도 판단
- Prompts — 디버깅/멀티코어 워크플로우 가이드
- Resources — 사용 가이드 + 코어별 상태 조회 (resource templates)
- Logging — tool 실행/에러 로그 실시간 전송
- Progress — 장시간 작업(connect_all 등) 진행률 보고
- Cancellation — 진행 중인 작업 취소 지원
- Completion — prompt/resource 이름 자동완성

**MCP 설정 예시** (`claude_desktop_config.json` 또는 `.mcp.json`):
```json
{
  "mcpServers": {
    "trace32": {
      "command": "python",
      "args": ["<프로젝트경로>/mcp_server.py"]
    }
  }
}
```

### 2-B. HTTP REST API Server

```bash
python http_server.py
python http_server.py --listen 0.0.0.0 --http-port 8032 --host 10.0.0.5 --port 20000
```

```bash
# 연결
curl -X POST http://localhost:8032/api/connect \
  -H "Content-Type: application/json" \
  -d '{"host":"localhost","port":20000}'

# 명령 실행
curl -X POST http://localhost:8032/api/cmd \
  -d '{"command":"SYStem.Up"}'

# 상태 조회
curl http://localhost:8032/api/state

# 메모리 읽기
curl -X POST http://localhost:8032/api/memory/read \
  -d '{"address":"0x08000000","size":256}'

# API 목록
curl http://localhost:8032/api/tools
```

### 2-C. Python Library 직접 사용

```python
from t32 import Trace32Client

client = Trace32Client()
client.connect('localhost', 20000)

# 기본 제어
client.system_up()
client.load_elf('firmware.elf')

# 실행 제어
client.set_breakpoint(0x08001000)
client.go()
state = client.get_state()  # {'state_code': 2, 'state_name': 'stopped'}

# 레지스터/메모리
pc = client.read_pc()
r0 = client.read_register('R0')
data = client.read_memory(0x20000000, 256)
client.write_memory(0x20000000, b'\x00' * 256)

# 변수/심볼
value = client.read_variable('myGlobalVar')
addr = client.get_symbol_address('main')

# PRACTICE 스크립트
client.run_script('setup.cmm')

# 아무 TRACE32 명령이든 실행 가능
client.cmd('Data.dump D:0x0--0xFF')
result = client.eval_expression('VERSION.SOFTWARE()')

client.disconnect()
```

## 제공 도구 (MCP Tools / HTTP API)

| MCP Tool | HTTP Endpoint | 설명 |
|----------|--------------|------|
| `t32_connect` | `POST /api/connect` | TRACE32에 연결 |
| `t32_connect_all` | `POST /api/connect_all` | 멀티코어 일괄 연결 |
| `t32_disconnect` | `POST /api/disconnect` | 연결 해제 |
| `t32_disconnect_all` | `POST /api/disconnect_all` | 전체 연결 해제 |
| `t32_list_cores` | `GET /api/cores` | 접속된 코어 목록 |
| `t32_set_endian` | `POST /api/endian/set` | 코어별 엔디안 설정 |
| `t32_get_endian` | `POST /api/endian/get` | 엔디안 조회 |
| `t32_cmd` | `POST /api/cmd` | PRACTICE 명령 실행 |
| `t32_eval` | `POST /api/eval` | 수식 평가 후 결과 반환 |
| `t32_get_state` | `GET /api/state` | 타겟 CPU 상태 조회 |
| `t32_read_memory` | `POST /api/memory/read` | 메모리 읽기 (hex) |
| `t32_write_memory` | `POST /api/memory/write` | 메모리 쓰기 |
| `t32_read_register` | `POST /api/register/read` | 레지스터 읽기 |
| `t32_write_register` | `POST /api/register/write` | 레지스터 쓰기 |
| `t32_go` | `POST /api/go` | 실행 시작 |
| `t32_break` | `POST /api/break` | 실행 중지 |
| `t32_step` | `POST /api/step` | 싱글 스텝 |
| `t32_breakpoint_set` | `POST /api/breakpoint/set` | 브레이크포인트 설정 |
| `t32_breakpoint_delete` | `POST /api/breakpoint/delete` | 브레이크포인트 삭제 |
| `t32_breakpoint_list` | `GET /api/breakpoint/list` | 브레이크포인트 목록 |
| `t32_read_variable` | `POST /api/variable/read` | C 변수 읽기 |
| `t32_write_variable` | `POST /api/variable/write` | C 변수 쓰기 |
| `t32_get_symbol` | `POST /api/symbol` | 심볼 주소 조회 |
| `t32_run_script` | `POST /api/script/run` | .cmm 스크립트 실행 |
| `t32_load` | `POST /api/load` | ELF/바이너리 로드 |
| `t32_get_version` | `GET /api/version` | TRACE32 버전 조회 |
| `t32_memory_dump` | — | 메모리 → 파일 저장 (binary/text) |
| `t32_memory_load` | — | 파일 → 메모리 로드 (binary/text) |
| `t32_start` | — | t32start.exe로 PowerView 인스턴스 실행 |
| `t32_ping` | `GET /api/ping` | 연결 상태 확인 (ping) |
| `t32_get_cpu` | `GET /api/cpu` | 타겟 CPU 이름 조회 |
| `t32_reset` | `POST /api/reset` | 타겟 CPU 리셋 |
| `t32_system_up` | `POST /api/system/up` | 디버거↔타겟 연결 (SYStem.Up) |
| `t32_system_down` | `POST /api/system/down` | 디버거↔타겟 해제 (SYStem.Down) |
| `t32_get_practice_state` | `GET /api/practice/state` | PRACTICE 스크립트 실행 상태 |
| `t32_get_message` | `GET /api/message` | AREA 윈도우 메시지 조회 |

### 메모리 덤프/로드 (`t32_memory_dump` / `t32_memory_load`)

MCP 서버 호스트의 파일시스템에 메모리를 저장하거나 파일에서 메모리로 로드합니다.

**`t32_memory_dump` 파라미터:**

| 파라미터 | 필수 | 설명 |
|---------|------|------|
| `address` | O | 시작 주소 (예: `0x1000`, `"D:0x1000"`) |
| `size` | O | 덤프할 바이트 수 |
| `path` | O | 저장할 파일 경로 (MCP 서버 호스트) |
| `access` | | 접근 클래스: `D`, `P`, `SD`, `SP` (기본: `D`) |
| `format` | | `bin` (raw binary, 기본) 또는 `text` (T32 스타일 hex dump) |
| `core_id` | | 코어 ID (0-15, 기본: 0) |

**`t32_memory_load` 파라미터:**

| 파라미터 | 필수 | 설명 |
|---------|------|------|
| `address` | O | 타겟 시작 주소 |
| `path` | O | 읽을 파일 경로 (MCP 서버 호스트) |
| `access` | | 접근 클래스 (기본: `D`) |
| `format` | | `bin` (기본) 또는 `text` |
| `core_id` | | 코어 ID (0-15, 기본: 0) |

**text 포맷 출력 예시** (T32 Data.dump 스타일):

```
D:0x08000000: 00 20 00 20 C1 02 00 08 B5 02 00 08 B7 02 00 08  |. . ............|
D:0x08000010: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  |................|
D:0x08000020: 00 00 00 00 00 00 00 00 00 00 00 00 BD 02 00 08  |................|
```

각 줄은 `[접근클래스:주소]: [16바이트 hex]  |[ASCII]|` 형식입니다.
- 16바이트 단위로 한 줄 출력
- ASCII 범위(0x20-0x7E) 외 바이트는 `.`으로 표시
- 마지막 줄은 16바이트 미만일 수 있음

**MCP 사용 예시:**

```json
// 메모리 → 바이너리 파일 저장
{"name": "t32_memory_dump", "arguments": {
  "address": "0x08000000", "size": 4096, "path": "/tmp/flash.bin"
}}

// 메모리 → 텍스트 덤프 저장
{"name": "t32_memory_dump", "arguments": {
  "address": "D:0x20000000", "size": 256, "path": "/tmp/ram.txt", "format": "text"
}}

// 바이너리 파일 → 메모리 로드
{"name": "t32_memory_load", "arguments": {
  "address": "0x08000000", "path": "/tmp/flash.bin"
}}

// 텍스트 덤프 → 메모리 로드
{"name": "t32_memory_load", "arguments": {
  "address": "0x20000000", "path": "/tmp/ram.txt", "format": "text"
}}
```

## 테스트

```bash
# Python 3.5+ (pytest 지원)
python -m pytest tests/ -v

# Python 2.7 / 3.4 (pytest 5.0+는 3.4 미지원이므로 unittest 사용)
python -m unittest discover -s tests -p "test_*.py" -v
```

Mock UDP 서버를 사용하므로 실제 TRACE32 하드웨어 없이 전체 테스트 가능 (340개 테스트).

## 프로토콜 참고

TRACE32 RCL (Remote Control) NETASSIST 프로토콜 구현 기반:
- UDP 패킷: `[타입:1][플래그:1][시퀀스:2][데이터]`
- 메시지: `[LEN][CMD][SUBCMD][MSGID][페이로드]`
- 연결: UDP 핸드셰이크 → 3-way Sync → ATTACH
- 레퍼런스: `<T32_DIR>/demo/api/capi/src/hremote.c`, `hlinknet.c`

**참고**: NETTCP(TCP)는 PowerDebug X50에서만 지원됩니다. PowerDebug II/III 등 이전 모델은 NETASSIST(UDP)를 사용해야 합니다.

## 향후 확장 가능 방향

- [ ] ctypes 기반 t32api.dll 래퍼 (lib/ 디렉토리에 DLL 복사 후 사용)
- [x] PRACTICE 스크립트 실행 상태 모니터링 (`t32_get_practice_state`)
- [x] 메모리 덤프 파일 저장/로드 (`t32_memory_dump` / `t32_memory_load`)
- [ ] WebSocket 실시간 이벤트 스트리밍
