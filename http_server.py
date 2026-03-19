#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 HTTP REST API Server.

Provides a simple HTTP/JSON API for controlling TRACE32 PowerView.
Useful when MCP is not available or when integrating with tools
that prefer HTTP APIs (curl, Postman, custom scripts, etc.).

Compatible with Python 2.7 and 3.4+. No external dependencies.

Usage:
    python http_server.py
    python http_server.py --listen 0.0.0.0 --http-port 8032
    python http_server.py --host 10.0.0.5 --port 20000

API Examples:
    POST /api/connect       {"host":"localhost","port":20000}
    POST /api/cmd           {"command":"SYStem.Up"}
    POST /api/eval          {"expression":"Register(PC)"}
    GET  /api/state
    POST /api/memory/read   {"address":"0x1000","size":256}
    POST /api/register/read {"name":"PC"}
    POST /api/go
    POST /api/break
"""
from __future__ import print_function

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

from t32.client import Trace32Client, Trace32Error

# Global client
_client = Trace32Client()


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


# ======================================================================
# Route handlers
# ======================================================================

def _api_connect(body):
    host = body.get("host", "localhost")
    port = int(body.get("port", 20000))
    _client.connect(host=host, port=port)
    version = ""
    try:
        version = _client.get_version()
    except Exception:
        pass
    r = {"status": "connected", "host": host, "port": port}
    if version:
        r["version"] = version
    return r


def _api_disconnect(body):
    _client.disconnect()
    return {"status": "disconnected"}


def _api_cmd(body):
    command = body["command"]
    _client.cmd(command)
    return {"status": "ok", "command": command}


def _api_eval(body):
    expr = body["expression"]
    result = _client.eval_expression(expr)
    return {"expression": expr, "result": result}


def _api_state(body):
    return _client.get_state()


def _api_memory_read(body):
    addr = body["address"]
    size = int(body["size"])
    access = body.get("access", "D")
    hex_data = _client.read_memory_hex(addr, size, access)
    return {"address": str(addr), "size": size, "hex": hex_data}


def _api_memory_write(body):
    addr = body["address"]
    data = body["data"]
    access = body.get("access", "D")
    _client.write_memory(addr, data, access)
    return {"status": "ok", "address": str(addr), "bytes_written": len(data) // 2}


def _api_register_read(body):
    name = body["name"]
    value = _client.read_register(name)
    return {"register": name, "value": value, "hex": "0x{0:X}".format(value)}


def _api_register_write(body):
    name = body["name"]
    value = int(body["value"])
    _client.write_register(name, value)
    return {"status": "ok", "register": name, "value": value}


def _api_go(body):
    _client.go()
    return {"status": "ok", "action": "go"}


def _api_break(body):
    _client.break_target()
    return {"status": "ok", "action": "break"}


def _api_step(body):
    count = int(body.get("count", 1))
    over = body.get("over", False)
    if over:
        _client.step_over()
    else:
        _client.step(count)
    return {"status": "ok", "steps": count, "over": over}


def _api_breakpoint_set(body):
    address = body["address"]
    bp_type = body.get("type", "program")
    if isinstance(address, str) and not address.startswith('0x'):
        _client.cmd("Break.Set {0} /{1}".format(address, bp_type.capitalize()))
    else:
        _client.set_breakpoint(address, bp_type)
    return {"status": "ok", "address": str(address), "type": bp_type}


def _api_breakpoint_delete(body):
    address = body.get("address")
    _client.delete_breakpoint(address)
    return {"status": "ok", "address": str(address) if address else "all"}


def _api_breakpoint_list(body):
    result = _client.list_breakpoints()
    return {"breakpoints": result}


def _api_variable_read(body):
    name = body["name"]
    value = _client.read_variable(name)
    return {"variable": name, "value": value}


def _api_variable_write(body):
    name = body["name"]
    value = body["value"]
    _client.write_variable(name, value)
    return {"status": "ok", "variable": name, "value": value}


def _api_symbol(body):
    name = body["name"]
    address = _client.get_symbol_address(name)
    return {"symbol": name, "address": address}


def _api_script_run(body):
    path = body["path"]
    _client.run_script(path)
    return {"status": "ok", "script": path}


def _api_load(body):
    path = body["path"]
    fmt = body.get("format", "elf")
    if fmt == "elf":
        _client.load_elf(path)
    elif fmt == "binary":
        addr = body.get("address", 0)
        _client.load_binary(path, addr)
    return {"status": "ok", "path": path, "format": fmt}


def _api_version(body):
    version = _client.get_version()
    return {"version": version}


def _api_ping(body):
    _client.ping()
    return {"status": "ok"}


# POST routes
_POST_ROUTES = {
    '/api/connect': _api_connect,
    '/api/disconnect': _api_disconnect,
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
}


# ======================================================================
# HTTP Request Handler
# ======================================================================

class Trace32Handler(BaseHTTPRequestHandler):
    """HTTP request handler for TRACE32 REST API."""

    def log_message(self, fmt, *args):
        """Log to stderr."""
        sys.stderr.write("[HTTP] {0}\n".format(fmt % args))
        sys.stderr.flush()

    def do_OPTIONS(self):
        """Handle CORS preflight."""
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

        try:
            result = handler({})
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

    # Simple argument parsing compatible with Python 2.7
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
        elif args[i] in ('-h', '--help'):
            print("TRACE32 HTTP REST API Server")
            print("Usage: python http_server.py [options]")
            print("  --listen ADDR    Listen address (default: 127.0.0.1)")
            print("  --http-port PORT HTTP port (default: 8032)")
            print("  --host HOST      TRACE32 host (default: localhost)")
            print("  --port PORT      TRACE32 RCL port (default: 20000)")
            sys.exit(0)
        else:
            i += 1

    # Auto-connect to TRACE32 if possible
    try:
        _client.connect(host=t32_host, port=t32_port)
        sys.stderr.write("Connected to TRACE32 at {0}:{1}\n".format(t32_host, t32_port))
    except Trace32Error as e:
        sys.stderr.write("Warning: Could not connect to TRACE32: {0}\n".format(e))
        sys.stderr.write("Use POST /api/connect to connect later.\n")

    server = HTTPServer((listen, http_port), Trace32Handler)
    sys.stderr.write("TRACE32 HTTP API server listening on http://{0}:{1}\n".format(listen, http_port))
    sys.stderr.write("API docs: GET http://{0}:{1}/api/tools\n".format(listen, http_port))
    sys.stderr.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

    server.server_close()
    _client.disconnect()
    sys.stderr.write("Server stopped.\n")


if __name__ == '__main__':
    main()
