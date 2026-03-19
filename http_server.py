#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 HTTP REST API Server.

Provides a simple HTTP/JSON API for controlling TRACE32 PowerView.
Supports multi-core debugging (up to 16 cores on consecutive ports).

Compatible with Python 2.7 and 3.4+. No external dependencies.

Usage:
    python http_server.py
    python http_server.py --listen 0.0.0.0 --http-port 8032
    python http_server.py --host 10.0.0.5 --port 20000

Multi-core:
    python http_server.py --host 10.0.0.5 --base-port 20000 --num-cores 16

API Examples:
    POST /api/connect       {"host":"localhost","port":20000,"core_id":0}
    POST /api/connect_all   {"host":"localhost","base_port":20000,"num_cores":16}
    GET  /api/cores
    POST /api/cmd           {"command":"SYStem.Up","core_id":0}
    POST /api/eval          {"expression":"Register(PC)","core_id":3}
    GET  /api/state?core_id=0
    POST /api/memory/read   {"address":"0x1000","size":256,"core_id":5}
    POST /api/register/read {"name":"PC","core_id":2}
"""
from __future__ import print_function

import binascii
import json
import sys
import os

try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
except ImportError:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler

try:
    from urllib.parse import urlparse, parse_qs
except ImportError:
    from urlparse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from t32.client import Trace32Error
from t32.core_manager import CoreManager, interpret_words

# Global core manager
_core_manager = CoreManager()


def _json_response(handler, status, data):
    """Send a JSON HTTP response."""
    body = json.dumps(data, indent=2, ensure_ascii=False)
    if sys.version_info[0] >= 3:
        body = body.encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler):
    """Read and parse JSON body from request."""
    length = int(handler.headers.get('Content-Length', 0) if hasattr(handler.headers, 'get')
                 else handler.headers.getheader('Content-Length', 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    return json.loads(raw)


def _get_core_id(body):
    """Extract core_id from body dict, default 0."""
    return int(body.get("core_id", 0))


def _get_client(body):
    """Get Trace32Client for the core_id in body."""
    return _core_manager.get_client(_get_core_id(body))


# ======================================================================
# Route handlers
# ======================================================================

def _api_connect(body):
    host = body.get("host", "localhost")
    port = int(body.get("port", 20000))
    core_id = _get_core_id(body)
    client = _core_manager.connect_core(core_id, host, port)
    version = ""
    try:
        version = client.get_version()
    except Exception:
        pass
    r = {"status": "connected", "core_id": core_id, "host": host, "port": port}
    if version:
        r["version"] = version
    return r


def _api_connect_all(body):
    host = body.get("host", "localhost")
    base_port = int(body.get("base_port", 20000))
    num_cores = int(body.get("num_cores", 16))
    results = _core_manager.connect_all(host, base_port, num_cores)
    connected = sum(1 for r in results if r["status"] == "connected")
    return {
        "total": num_cores,
        "connected": connected,
        "failed": num_cores - connected,
        "cores": results,
    }


def _api_disconnect(body):
    core_id = _get_core_id(body)
    _core_manager.disconnect_core(core_id)
    return {"status": "disconnected", "core_id": core_id}


def _api_disconnect_all(body):
    count = _core_manager.connected_count
    _core_manager.disconnect_all()
    return {"status": "disconnected_all", "cores_disconnected": count}


def _api_cores(body):
    return {
        "connected_count": _core_manager.connected_count,
        "cores": _core_manager.list_cores(),
    }


def _api_endian_set(body):
    core_id = _get_core_id(body)
    endian = body["endian"]
    _core_manager.set_endianness(core_id, endian)
    return {"status": "ok", "core_id": core_id, "endian": endian}


def _api_endian_get(body):
    core_id = _get_core_id(body)
    endian = _core_manager.get_endianness(core_id)
    return {"core_id": core_id, "endian": endian}


def _api_cmd(body):
    command = body["command"]
    client = _get_client(body)
    client.cmd(command)
    return {"status": "ok", "command": command}


def _api_eval(body):
    client = _get_client(body)
    expr = body["expression"]
    result = client.eval_expression(expr)
    return {"expression": expr, "result": result}


def _api_state(body):
    client = _get_client(body)
    return client.get_state()


def _api_memory_read(body):
    client = _get_client(body)
    core_id = _get_core_id(body)
    addr = body["address"]
    size = int(body["size"])
    access = body.get("access", "D")
    raw_data = client.read_memory(addr, size, access)
    hex_data = binascii.hexlify(raw_data).decode('ascii').upper()
    endian = _core_manager.get_endianness(core_id)
    result = {"address": str(addr), "size": size, "endian": endian, "hex": hex_data}

    word_size = body.get("word_size")
    if word_size is not None:
        word_size = int(word_size)
        words = interpret_words(raw_data, word_size, endian)
        fmt = "0x{{0:0{0}X}}".format(word_size * 2)
        result["word_size"] = word_size
        result["words"] = words
        result["words_hex"] = [fmt.format(w) for w in words]

    return result


def _api_memory_write(body):
    client = _get_client(body)
    addr = body["address"]
    data = body["data"]
    access = body.get("access", "D")
    client.write_memory(addr, data, access)
    return {"status": "ok", "address": str(addr), "bytes_written": len(data) // 2}


def _api_register_read(body):
    client = _get_client(body)
    name = body["name"]
    value = client.read_register(name)
    return {"register": name, "value": value, "hex": "0x{0:X}".format(value)}


def _api_register_write(body):
    client = _get_client(body)
    name = body["name"]
    value = int(body["value"])
    client.write_register(name, value)
    return {"status": "ok", "register": name, "value": value}


def _api_go(body):
    client = _get_client(body)
    client.go()
    return {"status": "ok", "action": "go"}


def _api_break(body):
    client = _get_client(body)
    client.break_target()
    return {"status": "ok", "action": "break"}


def _api_step(body):
    client = _get_client(body)
    count = int(body.get("count", 1))
    over = body.get("over", False)
    if over:
        client.step_over()
    else:
        client.step(count)
    return {"status": "ok", "steps": count, "over": over}


def _api_breakpoint_set(body):
    client = _get_client(body)
    address = body["address"]
    bp_type = body.get("type", "program")
    if isinstance(address, str) and not address.startswith('0x'):
        client.cmd("Break.Set {0} /{1}".format(address, bp_type.capitalize()))
    else:
        client.set_breakpoint(address, bp_type)
    return {"status": "ok", "address": str(address), "type": bp_type}


def _api_breakpoint_delete(body):
    client = _get_client(body)
    address = body.get("address")
    client.delete_breakpoint(address)
    return {"status": "ok", "address": str(address) if address else "all"}


def _api_breakpoint_list(body):
    client = _get_client(body)
    result = client.list_breakpoints()
    return {"breakpoints": result}


def _api_variable_read(body):
    client = _get_client(body)
    name = body["name"]
    value = client.read_variable(name)
    return {"variable": name, "value": value}


def _api_variable_write(body):
    client = _get_client(body)
    name = body["name"]
    value = body["value"]
    client.write_variable(name, value)
    return {"status": "ok", "variable": name, "value": value}


def _api_symbol(body):
    client = _get_client(body)
    name = body["name"]
    address = client.get_symbol_address(name)
    return {"symbol": name, "address": address}


def _api_script_run(body):
    client = _get_client(body)
    path = body["path"]
    client.run_script(path)
    return {"status": "ok", "script": path}


def _api_load(body):
    client = _get_client(body)
    path = body["path"]
    fmt = body.get("format", "elf")
    if fmt == "elf":
        client.load_elf(path)
    elif fmt == "binary":
        addr = body.get("address", 0)
        client.load_binary(path, addr)
    return {"status": "ok", "path": path, "format": fmt}


def _api_version(body):
    client = _get_client(body)
    version = client.get_version()
    return {"version": version}


def _api_ping(body):
    client = _get_client(body)
    client.ping()
    return {"status": "ok"}


# POST routes
_POST_ROUTES = {
    '/api/connect': _api_connect,
    '/api/connect_all': _api_connect_all,
    '/api/disconnect': _api_disconnect,
    '/api/disconnect_all': _api_disconnect_all,
    '/api/endian/set': _api_endian_set,
    '/api/cmd': _api_cmd,
    '/api/eval': _api_eval,
    '/api/memory/read': _api_memory_read,
    '/api/memory/write': _api_memory_write,
    '/api/register/read': _api_register_read,
    '/api/register/write': _api_register_write,
    '/api/go': _api_go,
    '/api/break': _api_break,
    '/api/step': _api_step,
    '/api/breakpoint/set': _api_breakpoint_set,
    '/api/breakpoint/delete': _api_breakpoint_delete,
    '/api/variable/read': _api_variable_read,
    '/api/variable/write': _api_variable_write,
    '/api/symbol': _api_symbol,
    '/api/script/run': _api_script_run,
    '/api/load': _api_load,
}

# GET routes
_GET_ROUTES = {
    '/api/state': _api_state,
    '/api/breakpoint/list': _api_breakpoint_list,
    '/api/version': _api_version,
    '/api/ping': _api_ping,
    '/api/cores': _api_cores,
    '/api/endian': _api_endian_get,
}


# ======================================================================
# HTTP Request Handler
# ======================================================================

class Trace32Handler(BaseHTTPRequestHandler):
    """HTTP request handler for TRACE32 REST API."""

    def log_message(self, fmt, *args):
        sys.stderr.write("[HTTP] {0}\n".format(fmt % args))
        sys.stderr.flush()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/api/tools' or path == '/api':
            routes = []
            for p in sorted(_POST_ROUTES.keys()):
                routes.append({"method": "POST", "path": p})
            for p in sorted(_GET_ROUTES.keys()):
                routes.append({"method": "GET", "path": p})
            _json_response(self, 200, {"tools": routes})
            return

        handler = _GET_ROUTES.get(path)
        if handler is None:
            _json_response(self, 404, {"error": "Not found: " + path})
            return

        # Extract query params (supports ?core_id=N)
        qs = parse_qs(parsed.query)
        body = {}
        for key in qs:
            body[key] = qs[key][0]

        try:
            result = handler(body)
            _json_response(self, 200, result)
        except Trace32Error as e:
            _json_response(self, 500, {"error": str(e)})
        except Exception as e:
            _json_response(self, 500, {"error": str(e)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        handler = _POST_ROUTES.get(path)
        if handler is None:
            _json_response(self, 404, {"error": "Not found: " + path})
            return

        try:
            body = _read_body(self)
            result = handler(body)
            _json_response(self, 200, result)
        except Trace32Error as e:
            _json_response(self, 500, {"error": str(e)})
        except KeyError as e:
            _json_response(self, 400, {"error": "Missing required field: " + str(e)})
        except Exception as e:
            _json_response(self, 500, {"error": str(e)})


# ======================================================================
# Main
# ======================================================================

def main():
    listen = '127.0.0.1'
    http_port = 8032
    t32_host = 'localhost'
    t32_port = 20000
    base_port = None
    num_cores = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--listen' and i + 1 < len(args):
            listen = args[i + 1]
            i += 2
        elif args[i] == '--http-port' and i + 1 < len(args):
            http_port = int(args[i + 1])
            i += 2
        elif args[i] == '--host' and i + 1 < len(args):
            t32_host = args[i + 1]
            i += 2
        elif args[i] == '--port' and i + 1 < len(args):
            t32_port = int(args[i + 1])
            i += 2
        elif args[i] == '--base-port' and i + 1 < len(args):
            base_port = int(args[i + 1])
            i += 2
        elif args[i] == '--num-cores' and i + 1 < len(args):
            num_cores = int(args[i + 1])
            i += 2
        elif args[i] in ('-h', '--help'):
            print("TRACE32 HTTP REST API Server")
            print("Usage: python http_server.py [options]")
            print("  --listen ADDR       Listen address (default: 127.0.0.1)")
            print("  --http-port PORT    HTTP port (default: 8032)")
            print("  --host HOST         TRACE32 host (default: localhost)")
            print("  --port PORT         TRACE32 RCL port (default: 20000)")
            print("  --base-port PORT    Multi-core base port")
            print("  --num-cores N       Number of cores (enables multi-core)")
            sys.exit(0)
        else:
            i += 1

    # Auto-connect
    if num_cores and base_port:
        results = _core_manager.connect_all(t32_host, base_port, num_cores)
        connected = sum(1 for r in results if r["status"] == "connected")
        sys.stderr.write("Connected {0}/{1} cores at {2}:{3}-{4}\n".format(
            connected, num_cores, t32_host, base_port, base_port + num_cores - 1))
    else:
        try:
            _core_manager.connect_core(0, t32_host, t32_port)
            sys.stderr.write("Connected to TRACE32 at {0}:{1}\n".format(t32_host, t32_port))
        except Trace32Error as e:
            sys.stderr.write("Warning: Could not connect to TRACE32: {0}\n".format(e))
            sys.stderr.write("Use POST /api/connect to connect later.\n")

    server = HTTPServer((listen, http_port), Trace32Handler)
    sys.stderr.write("TRACE32 HTTP API server listening on http://{0}:{1}\n".format(listen, http_port))
    sys.stderr.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    server.server_close()
    _core_manager.disconnect_all()
    sys.stderr.write("Server stopped.\n")


if __name__ == '__main__':
    main()
