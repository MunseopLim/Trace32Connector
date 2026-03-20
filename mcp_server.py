#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 MCP Server - Model Context Protocol server for TRACE32 debugger.

Implements MCP (stdio transport) so AI assistants like Claude can
interact with TRACE32 PowerView for embedded debugging.

Supports multi-core debugging (up to 16 cores on consecutive ports).

Compatible with Python 2.7 and 3.4+. No external dependencies.

Usage:
    python mcp_server.py
    python mcp_server.py --host localhost --port 20000
"""
from __future__ import print_function

import binascii
import json
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from t32.client import Trace32Error
from t32.core_manager import CoreManager, interpret_words

# Global core manager (replaces single _client)
_core_manager = CoreManager()

# Shared schema property for core_id
_CORE_ID_PROP = {
    "core_id": {
        "type": "integer",
        "description": "Core ID for multi-core debugging (0-15, default: 0)",
        "default": 0
    }
}

# ======================================================================
# Tool Definitions
# ======================================================================


def _inject_core_id(schema):
    """Add core_id property to an inputSchema dict."""
    if schema is None:
        return {
            "type": "object",
            "properties": dict(_CORE_ID_PROP)
        }
    props = dict(schema.get("properties", {}))
    props.update(_CORE_ID_PROP)
    result = dict(schema)
    result["properties"] = props
    return result


TOOLS = [
    {
        "name": "t32_connect",
        "description": (
            "Connect to a running TRACE32 PowerView instance. "
            "PowerView must have RCL=NETASSIST enabled in config.t32. "
            "Use core_id for multi-core setups (0-15)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Hostname or IP (default: localhost)",
                    "default": "localhost"
                },
                "port": {
                    "type": "integer",
                    "description": "RCL port number (default: 20000)",
                    "default": 20000
                },
                "core_id": {
                    "type": "integer",
                    "description": "Core ID for multi-core (0-15, default: 0)",
                    "default": 0
                }
            }
        }
    },
    {
        "name": "t32_connect_all",
        "description": (
            "Connect to multiple TRACE32 cores at once. "
            "Connects to num_cores consecutive ports starting from base_port."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Hostname or IP (default: localhost)",
                    "default": "localhost"
                },
                "base_port": {
                    "type": "integer",
                    "description": "First port number (default: 20000)",
                    "default": 20000
                },
                "num_cores": {
                    "type": "integer",
                    "description": "Number of cores to connect (1-16)",
                    "default": 16
                }
            },
            "required": ["num_cores"]
        }
    },
    {
        "name": "t32_disconnect",
        "description": "Disconnect a single core from TRACE32 PowerView.",
        "inputSchema": _inject_core_id(None)
    },
    {
        "name": "t32_disconnect_all",
        "description": "Disconnect all connected TRACE32 cores."
    },
    {
        "name": "t32_list_cores",
        "description": "List all connected cores with their host, port, endian, and status."
    },
    {
        "name": "t32_set_endian",
        "description": (
            "Set target endianness for a core. Affects word-level memory interpretation. "
            "Default is 'little'. Use 'big' for big-endian targets (PowerPC, some ARM BE8)."
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "endian": {
                    "type": "string",
                    "description": "Target endianness: 'little' or 'big'",
                    "enum": ["little", "big"]
                }
            },
            "required": ["endian"]
        })
    },
    {
        "name": "t32_get_endian",
        "description": "Get the current target endianness setting for a core.",
        "inputSchema": _inject_core_id(None)
    },
    {
        "name": "t32_cmd",
        "description": (
            "Execute any TRACE32 PRACTICE command. This is the most versatile tool. "
            "Any command you can type in the TRACE32 command line works here. "
            "Examples: 'SYStem.Up', 'Break.Set main', 'Data.dump 0x0--0xFF', "
            "'Var.Watch myVar', 'Register.view'"
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "TRACE32 PRACTICE command to execute"
                }
            },
            "required": ["command"]
        })
    },
    {
        "name": "t32_eval",
        "description": (
            "Evaluate a TRACE32 expression and return the result as text. "
            "Examples: 'Register(PC)', 'Var.VALUE(myVar)', "
            "'sYmbol.BEGIN(main)', 'VERSION.SOFTWARE()'"
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "TRACE32 expression to evaluate"
                }
            },
            "required": ["expression"]
        })
    },
    {
        "name": "t32_get_state",
        "description": (
            "Get the current target CPU state. "
            "Returns: down, halted, stopped (at breakpoint), or running."
        ),
        "inputSchema": _inject_core_id(None)
    },
    {
        "name": "t32_read_memory",
        "description": (
            "Read target memory. Returns hex-encoded bytes. "
            "Address can include access class prefix like 'D:0x1000' or 'P:0x2000'. "
            "Use word_size (2 or 4) for endian-aware word interpretation."
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "address": {
                    "type": ["integer", "string"],
                    "description": "Memory address (e.g. 0x1000 or 'D:0x1000')"
                },
                "size": {
                    "type": "integer",
                    "description": "Number of bytes to read"
                },
                "access": {
                    "type": "string",
                    "description": "Access class: D (data), P (program), SD, SP (default: D)",
                    "default": "D"
                },
                "word_size": {
                    "type": "integer",
                    "description": "Word size for endian-aware interpretation (2 or 4 bytes). Omit for raw bytes only.",
                    "enum": [2, 4]
                }
            },
            "required": ["address", "size"]
        })
    },
    {
        "name": "t32_write_memory",
        "description": "Write data to target memory.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "address": {
                    "type": ["integer", "string"],
                    "description": "Memory address"
                },
                "data": {
                    "type": "string",
                    "description": "Hex string of data to write (e.g. 'DEADBEEF')"
                },
                "access": {
                    "type": "string",
                    "description": "Access class (default: D)",
                    "default": "D"
                }
            },
            "required": ["address", "data"]
        })
    },
    {
        "name": "t32_read_register",
        "description": (
            "Read a CPU register by name. "
            "Examples: PC, SP, R0-R15, LR, CPSR, etc."
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Register name (e.g. PC, SP, R0)"
                }
            },
            "required": ["name"]
        })
    },
    {
        "name": "t32_write_register",
        "description": "Write a value to a CPU register.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Register name"
                },
                "value": {
                    "type": "integer",
                    "description": "Value to write"
                }
            },
            "required": ["name", "value"]
        })
    },
    {
        "name": "t32_go",
        "description": "Start (resume) target CPU execution.",
        "inputSchema": _inject_core_id(None)
    },
    {
        "name": "t32_break",
        "description": "Halt (break) target CPU execution.",
        "inputSchema": _inject_core_id(None)
    },
    {
        "name": "t32_step",
        "description": "Single-step the target CPU.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of steps (default: 1)",
                    "default": 1
                },
                "over": {
                    "type": "boolean",
                    "description": "Step over function calls (default: false)",
                    "default": False
                }
            }
        })
    },
    {
        "name": "t32_breakpoint_set",
        "description": "Set a breakpoint at an address.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "address": {
                    "type": ["integer", "string"],
                    "description": "Breakpoint address (e.g. 0x1000 or symbol name via 'main')"
                },
                "type": {
                    "type": "string",
                    "description": "Breakpoint type: program, read, write, readwrite (default: program)",
                    "default": "program"
                }
            },
            "required": ["address"]
        })
    },
    {
        "name": "t32_breakpoint_delete",
        "description": "Delete breakpoint(s). Omit address to delete all.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "address": {
                    "type": ["integer", "string"],
                    "description": "Address to remove breakpoint from, or omit for all"
                }
            }
        })
    },
    {
        "name": "t32_breakpoint_list",
        "description": "List all currently set breakpoints.",
        "inputSchema": _inject_core_id(None)
    },
    {
        "name": "t32_read_variable",
        "description": "Read a C/C++ variable value from the target.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Variable name (e.g. 'myVar', 'myStruct.field', '*ptr')"
                }
            },
            "required": ["name"]
        })
    },
    {
        "name": "t32_write_variable",
        "description": "Write a value to a C/C++ variable on the target.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Variable name"
                },
                "value": {
                    "type": "string",
                    "description": "Value to write (as expression)"
                }
            },
            "required": ["name", "value"]
        })
    },
    {
        "name": "t32_get_symbol",
        "description": "Get the memory address of a symbol (function or global variable).",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Symbol name (e.g. 'main', 'myGlobalVar')"
                }
            },
            "required": ["name"]
        })
    },
    {
        "name": "t32_run_script",
        "description": "Execute a PRACTICE (.cmm) script file on the TRACE32 host.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to .cmm script (on the TRACE32 host filesystem)"
                }
            },
            "required": ["path"]
        })
    },
    {
        "name": "t32_load",
        "description": "Load a binary/ELF file to the target.",
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to file (on the TRACE32 host filesystem)"
                },
                "format": {
                    "type": "string",
                    "description": "File format: elf, binary (default: elf)",
                    "default": "elf"
                },
                "address": {
                    "type": ["integer", "string"],
                    "description": "Load address (required for binary format)"
                }
            },
            "required": ["path"]
        })
    },
    {
        "name": "t32_get_version",
        "description": "Get TRACE32 PowerView software version.",
        "inputSchema": _inject_core_id(None)
    },
]


# ======================================================================
# Tool Handlers
# ======================================================================

def _get_client(args):
    """Extract core_id from args and return the appropriate client."""
    core_id = int(args.get("core_id", 0))
    return _core_manager.get_client(core_id)


def _handle_connect(args):
    host = args.get("host", "localhost")
    port = args.get("port", 20000)
    core_id = int(args.get("core_id", 0))
    client = _core_manager.connect_core(core_id, host, port)
    version = ""
    try:
        version = client.get_version()
    except Exception:
        pass
    result = {"status": "connected", "core_id": core_id, "host": host, "port": port}
    if version:
        result["version"] = version
    return result


def _handle_connect_all(args):
    host = args.get("host", "localhost")
    base_port = int(args.get("base_port", 20000))
    num_cores = int(args.get("num_cores", 16))
    results = _core_manager.connect_all(host, base_port, num_cores)
    connected = sum(1 for r in results if r["status"] == "connected")
    return {
        "total": num_cores,
        "connected": connected,
        "failed": num_cores - connected,
        "cores": results,
    }


def _handle_disconnect(args):
    core_id = int(args.get("core_id", 0))
    _core_manager.disconnect_core(core_id)
    return {"status": "disconnected", "core_id": core_id}


def _handle_disconnect_all(args):
    count = _core_manager.connected_count
    _core_manager.disconnect_all()
    return {"status": "disconnected_all", "cores_disconnected": count}


def _handle_list_cores(args):
    cores = _core_manager.list_cores()
    return {
        "connected_count": _core_manager.connected_count,
        "cores": cores,
    }


def _handle_set_endian(args):
    core_id = int(args.get("core_id", 0))
    endian = args["endian"]
    _core_manager.set_endianness(core_id, endian)
    return {"status": "ok", "core_id": core_id, "endian": endian}


def _handle_get_endian(args):
    core_id = int(args.get("core_id", 0))
    endian = _core_manager.get_endianness(core_id)
    return {"core_id": core_id, "endian": endian}


def _handle_cmd(args):
    client = _get_client(args)
    command = args["command"]
    client.cmd(command)
    return {"status": "ok", "command": command}


def _handle_eval(args):
    client = _get_client(args)
    expression = args["expression"]
    result = client.eval_expression(expression)
    return {"expression": expression, "result": result}


def _handle_get_state(args):
    client = _get_client(args)
    return client.get_state()


def _handle_read_memory(args):
    client = _get_client(args)
    core_id = int(args.get("core_id", 0))
    address = args["address"]
    size = int(args["size"])
    access = args.get("access", "D")
    raw_data = client.read_memory(address, size, access)
    hex_data = binascii.hexlify(raw_data).decode('ascii').upper()
    endian = _core_manager.get_endianness(core_id)
    result = {"address": str(address), "size": size, "endian": endian, "hex": hex_data}

    word_size = args.get("word_size")
    if word_size is not None:
        word_size = int(word_size)
        words = interpret_words(raw_data, word_size, endian)
        fmt = "0x{{0:0{0}X}}".format(word_size * 2)
        result["word_size"] = word_size
        result["words"] = words
        result["words_hex"] = [fmt.format(w) for w in words]

    return result


def _handle_write_memory(args):
    client = _get_client(args)
    address = args["address"]
    data = args["data"]
    access = args.get("access", "D")
    client.write_memory(address, data, access)
    return {"status": "ok", "address": str(address), "bytes_written": len(data) // 2}


def _handle_read_register(args):
    client = _get_client(args)
    name = args["name"]
    value = client.read_register(name)
    return {"register": name, "value": value, "hex": "0x{0:X}".format(value)}


def _handle_write_register(args):
    client = _get_client(args)
    name = args["name"]
    value = int(args["value"])
    client.write_register(name, value)
    return {"status": "ok", "register": name, "value": value}


def _handle_go(args):
    client = _get_client(args)
    client.go()
    return {"status": "ok", "action": "target execution started"}


def _handle_break(args):
    client = _get_client(args)
    client.break_target()
    return {"status": "ok", "action": "target halted"}


def _handle_step(args):
    client = _get_client(args)
    count = int(args.get("count", 1))
    over = args.get("over", False)
    if over:
        client.step_over()
    else:
        client.step(count)
    return {"status": "ok", "steps": count, "over": over}


def _handle_breakpoint_set(args):
    client = _get_client(args)
    address = args["address"]
    bp_type = args.get("type", "program")
    if isinstance(address, str) and not address.startswith('0x') and not address.startswith('0X'):
        client.cmd("Break.Set {0} /{1}".format(address, bp_type.capitalize()))
    else:
        client.set_breakpoint(address, bp_type)
    return {"status": "ok", "address": str(address), "type": bp_type}


def _handle_breakpoint_delete(args):
    client = _get_client(args)
    address = args.get("address")
    client.delete_breakpoint(address)
    return {"status": "ok", "address": str(address) if address else "all"}


def _handle_breakpoint_list(args):
    client = _get_client(args)
    result = client.list_breakpoints()
    return {"breakpoints": result}


def _handle_read_variable(args):
    client = _get_client(args)
    name = args["name"]
    value = client.read_variable(name)
    return {"variable": name, "value": value}


def _handle_write_variable(args):
    client = _get_client(args)
    name = args["name"]
    value = args["value"]
    client.write_variable(name, value)
    return {"status": "ok", "variable": name, "value": value}


def _handle_get_symbol(args):
    client = _get_client(args)
    name = args["name"]
    address = client.get_symbol_address(name)
    return {"symbol": name, "address": address}


def _handle_run_script(args):
    client = _get_client(args)
    path = args["path"]
    client.run_script(path)
    return {"status": "ok", "script": path}


def _handle_load(args):
    client = _get_client(args)
    path = args["path"]
    fmt = args.get("format", "elf")
    if fmt == "elf":
        client.load_elf(path)
    elif fmt == "binary":
        address = args.get("address", 0)
        client.load_binary(path, address)
    else:
        client.cmd("Data.LOAD.{0} {1}".format(fmt.capitalize(), path))
    return {"status": "ok", "path": path, "format": fmt}


def _handle_get_version(args):
    client = _get_client(args)
    version = client.get_version()
    return {"version": version}


# Tool name -> handler mapping
_HANDLERS = {
    "t32_connect": _handle_connect,
    "t32_connect_all": _handle_connect_all,
    "t32_disconnect": _handle_disconnect,
    "t32_disconnect_all": _handle_disconnect_all,
    "t32_list_cores": _handle_list_cores,
    "t32_set_endian": _handle_set_endian,
    "t32_get_endian": _handle_get_endian,
    "t32_cmd": _handle_cmd,
    "t32_eval": _handle_eval,
    "t32_get_state": _handle_get_state,
    "t32_read_memory": _handle_read_memory,
    "t32_write_memory": _handle_write_memory,
    "t32_read_register": _handle_read_register,
    "t32_write_register": _handle_write_register,
    "t32_go": _handle_go,
    "t32_break": _handle_break,
    "t32_step": _handle_step,
    "t32_breakpoint_set": _handle_breakpoint_set,
    "t32_breakpoint_delete": _handle_breakpoint_delete,
    "t32_breakpoint_list": _handle_breakpoint_list,
    "t32_read_variable": _handle_read_variable,
    "t32_write_variable": _handle_write_variable,
    "t32_get_symbol": _handle_get_symbol,
    "t32_run_script": _handle_run_script,
    "t32_load": _handle_load,
    "t32_get_version": _handle_get_version,
}


# ======================================================================
# MCP Protocol Handler
# ======================================================================

def _make_response(req_id, result):
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id, code, message):
    """Build a JSON-RPC 2.0 error response."""
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_request(request):
    """Process a single JSON-RPC request and return a response dict (or None)."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    # Notifications (no id) don't get responses
    if req_id is None:
        return None

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "trace32-mcp-server",
                "version": "1.1.0"
            }
        }
        return _make_response(req_id, result)

    elif method == "ping":
        return _make_response(req_id, {})

    elif method == "tools/list":
        return _make_response(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = _HANDLERS.get(tool_name)
        if handler is None:
            return _make_response(req_id, {
                "content": [{"type": "text", "text": "Unknown tool: " + tool_name}],
                "isError": True
            })

        try:
            result = handler(tool_args)
            text = json.dumps(result, indent=2, ensure_ascii=False)
            return _make_response(req_id, {
                "content": [{"type": "text", "text": text}]
            })
        except Trace32Error as e:
            return _make_response(req_id, {
                "content": [{"type": "text", "text": "TRACE32 Error: " + str(e)}],
                "isError": True
            })
        except Exception as e:
            return _make_response(req_id, {
                "content": [{"type": "text", "text": "Error: " + str(e)}],
                "isError": True
            })

    elif method == "resources/list":
        return _make_response(req_id, {"resources": []})

    elif method == "prompts/list":
        return _make_response(req_id, {"prompts": []})

    else:
        return _make_error(req_id, -32601, "Method not found: " + method)


def _write_message(msg):
    """Write a JSON-RPC message to stdout (newline-delimited)."""
    line = json.dumps(msg, ensure_ascii=True)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main():
    """Main MCP server loop. Reads from stdin, writes to stdout."""
    sys.stderr.write("TRACE32 MCP Server v1.1.0 started (multi-core support). "
                     "Waiting for requests...\n")
    sys.stderr.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except ValueError as e:
                sys.stderr.write("Invalid JSON: {0}\n".format(e))
                sys.stderr.flush()
                continue

            response = _handle_request(request)
            if response is not None:
                _write_message(response)

        except KeyboardInterrupt:
            break
        except Exception as e:
            sys.stderr.write("Server error: {0}\n".format(traceback.format_exc()))
            sys.stderr.flush()

    # Cleanup
    try:
        _core_manager.disconnect_all()
    except Exception:
        pass
    sys.stderr.write("TRACE32 MCP Server stopped.\n")
    sys.stderr.flush()


if __name__ == '__main__':
    main()
