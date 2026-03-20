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

Claude Code 등에서 MCP 서버로 등록하면 26개 디버깅 도구를 AI가 직접 사용할 수 있습니다.
접속 후 30초 간격으로 keepalive ping이 자동 동작하여 idle 시에도 연결이 유지됩니다.

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

## 테스트

```bash
# Python 3.5+ (pytest 지원)
python -m pytest tests/ -v

# Python 2.7 / 3.4 (pytest 5.0+는 3.4 미지원이므로 unittest 사용)
python -m unittest discover -s tests -p "test_*.py" -v
```

Mock UDP 서버를 사용하므로 실제 TRACE32 하드웨어 없이 전체 테스트 가능 (221개 테스트).

## 프로토콜 참고

TRACE32 RCL (Remote Control) NETASSIST 프로토콜 구현 기반:
- UDP 패킷: `[타입:1][플래그:1][시퀀스:2][데이터]`
- 메시지: `[LEN][CMD][SUBCMD][MSGID][페이로드]`
- 연결: UDP 핸드셰이크 → 3-way Sync → ATTACH
- 레퍼런스: `<T32_DIR>/demo/api/capi/src/hremote.c`, `hlinknet.c`

**참고**: NETTCP(TCP)는 PowerDebug X50에서만 지원됩니다. PowerDebug II/III 등 이전 모델은 NETASSIST(UDP)를 사용해야 합니다.

## 향후 확장 가능 방향

- [ ] ctypes 기반 t32api.dll 래퍼 (lib/ 디렉토리에 DLL 복사 후 사용)
- [ ] PRACTICE 스크립트 실행 상태 모니터링 (polling)
- [ ] 메모리 덤프 파일 저장/로드
- [ ] WebSocket 실시간 이벤트 스트리밍
