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
import subprocess
import sys
import os
import time
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


# Tool annotations — hints for AI about tool behavior
# readOnlyHint: tool only reads, no side effects
# destructiveHint: tool may cause irreversible changes
# idempotentHint: calling multiple times with same args has same effect
# openWorldHint: tool interacts with external entities
_ANNOTATIONS = {
    "t32_connect":          {"title": "Connect to TRACE32", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_connect_all":      {"title": "Connect all cores", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_disconnect":       {"title": "Disconnect core", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_disconnect_all":   {"title": "Disconnect all cores", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_list_cores":       {"title": "List connected cores", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "t32_set_endian":       {"title": "Set endianness", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "t32_get_endian":       {"title": "Get endianness", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "t32_cmd":              {"title": "Execute PRACTICE command", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "t32_eval":             {"title": "Evaluate expression", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_get_state":        {"title": "Get CPU state", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_read_memory":      {"title": "Read memory", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_write_memory":     {"title": "Write memory", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "t32_read_register":    {"title": "Read register", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_write_register":   {"title": "Write register", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "t32_go":               {"title": "Resume execution", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_break":            {"title": "Halt execution", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_step":             {"title": "Single step", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "t32_breakpoint_set":   {"title": "Set breakpoint", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_breakpoint_delete": {"title": "Delete breakpoint", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "t32_breakpoint_list":  {"title": "List breakpoints", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_read_variable":    {"title": "Read variable", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_write_variable":   {"title": "Write variable", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "t32_get_symbol":       {"title": "Get symbol address", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_run_script":       {"title": "Run PRACTICE script", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "t32_load":             {"title": "Load firmware", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "t32_get_version":      {"title": "Get TRACE32 version", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_memory_dump":      {"title": "Dump memory to file", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    "t32_memory_load":      {"title": "Load file to memory", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "t32_start":            {"title": "Launch TRACE32 instance", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
}

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
            "Execute any TRACE32 PRACTICE command. "
            "Any command you can type in the TRACE32 command line works here. "
            "NOTE: This tool does NOT return any result text — it only reports success/failure. "
            "To READ a value, use t32_eval (expressions), t32_read_variable (C/C++ variables), "
            "or t32_read_register (registers) instead. "
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
        "description": (
            "Read a C/C++ variable value from the target. "
            "Use this tool to get the current value of any symbol, global variable, "
            "local variable, or struct field. Supports pointer dereference (*ptr) "
            "and nested access (myStruct.field). "
            "Internally uses Var.VALUE() via the TRACE32 eval protocol."
        ),
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
    {
        "name": "t32_memory_dump",
        "description": (
            "Read target memory and save to a file on the MCP server host. "
            "Supports binary (raw bytes) and text (T32-style hex dump with addresses and ASCII) formats."
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "address": {
                    "type": ["integer", "string"],
                    "description": "Start address (e.g. 0x1000 or 'D:0x1000')"
                },
                "size": {
                    "type": "integer",
                    "description": "Number of bytes to dump"
                },
                "path": {
                    "type": "string",
                    "description": "File path to save on the MCP server host"
                },
                "access": {
                    "type": "string",
                    "description": "Access class: D, P, SD, SP (default: D)",
                    "default": "D"
                },
                "format": {
                    "type": "string",
                    "description": "Output format: 'bin' (raw binary, default) or 'text' (T32-style hex dump with addresses)",
                    "enum": ["bin", "text"],
                    "default": "bin"
                }
            },
            "required": ["address", "size", "path"]
        })
    },
    {
        "name": "t32_memory_load",
        "description": (
            "Load a file from the MCP server host and write to target memory. "
            "Supports binary (raw bytes) and text (T32-style hex dump) formats."
        ),
        "inputSchema": _inject_core_id({
            "type": "object",
            "properties": {
                "address": {
                    "type": ["integer", "string"],
                    "description": "Target start address (e.g. 0x1000 or 'D:0x1000')"
                },
                "path": {
                    "type": "string",
                    "description": "File path to read from the MCP server host"
                },
                "access": {
                    "type": "string",
                    "description": "Access class: D, P, SD, SP (default: D)",
                    "default": "D"
                },
                "format": {
                    "type": "string",
                    "description": "Input format: 'bin' (raw binary, default) or 'text' (T32-style hex dump)",
                    "enum": ["bin", "text"],
                    "default": "bin"
                }
            },
            "required": ["address", "path"]
        })
    },
    {
        "name": "t32_start",
        "description": (
            "Launch a TRACE32 PowerView instance using t32start.exe. "
            "Starts a new T32 window with the specified configuration. "
            "After launch, use t32_connect to connect to it. "
            "The executable path can be given explicitly, or is auto-detected from "
            "T32_START or T32SYS environment variables, or system PATH."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "executable": {
                    "type": "string",
                    "description": (
                        "Path to t32start.exe. Optional if T32_START or T32SYS "
                        "environment variable is set, or t32start.exe is in PATH."
                    )
                },
                "runcfg": {
                    "type": "string",
                    "description": (
                        "Path to .ts2 configuration file (-runcfg). "
                        "Optional if T32_RUNCFG environment variable is set."
                    )
                },
                "runitem": {
                    "type": "string",
                    "description": (
                        "Configuration item name to launch from the .ts2 file (-runitem)"
                    )
                },
                "runaliases": {
                    "type": "string",
                    "description": (
                        "Alias definitions, semicolon-separated key=value pairs (-runaliases). "
                        "Example: 'CORE=0;PORT=20000'"
                    )
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Additional command-line arguments as a list. "
                        "Each flag and its value should be separate elements. "
                        "Example: ['-flag1', 'value1', '-flag2', 'value2']"
                    )
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for t32start.exe to finish (default: false)"
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds when wait=true (default: 30)"
                }
            },
            "required": []
        }
    },
]

# Inject annotations into each tool definition
for _tool in TOOLS:
    _ann = _ANNOTATIONS.get(_tool["name"])
    if _ann:
        _tool["annotations"] = _ann


# ======================================================================
# Prompts & Resources — guide AI to use MCP tools directly
# ======================================================================

PROMPTS = [
    {
        "name": "trace32-debug-workflow",
        "description": (
            "Standard workflow for TRACE32 debugging via MCP tools. "
            "Use this to understand the correct sequence of tool calls."
        ),
        "arguments": []
    },
    {
        "name": "trace32-multicore-workflow",
        "description": (
            "Workflow for multi-core debugging with TRACE32. "
            "Explains how to connect and control multiple cores."
        ),
        "arguments": []
    },
]

_PROMPT_CONTENTS = {
    "trace32-debug-workflow": (
        "# TRACE32 Debugging Workflow\n"
        "\n"
        "You have direct access to TRACE32 via MCP tools. "
        "Do NOT write Python scripts or use the HTTP API. "
        "Call the MCP tools directly.\n"
        "\n"
        "## Step-by-step\n"
        "1. **Connect**: Call `t32_connect` with host/port to establish a session.\n"
        "2. **Check state**: Call `t32_get_state` to see if the target is running, stopped, or down.\n"
        "3. **Load firmware** (if needed): Call `t32_load` with the ELF/binary path.\n"
        "4. **Set breakpoints**: Call `t32_breakpoint_set` with address or symbol name.\n"
        "5. **Run target**: Call `t32_go` to start execution.\n"
        "6. **Inspect**: When stopped, use:\n"
        "   - `t32_read_register` to read CPU registers (PC, SP, R0, etc.)\n"
        "   - `t32_read_memory` to read memory regions\n"
        "   - `t32_read_variable` to read C/C++ variables\n"
        "   - `t32_eval` to evaluate TRACE32 expressions\n"
        "7. **Step**: Call `t32_step` for single-stepping.\n"
        "8. **Execute commands**: Call `t32_cmd` for any PRACTICE command.\n"
        "9. **Disconnect**: Call `t32_disconnect` when done.\n"
        "\n"
        "## Important\n"
        "- Always call `t32_connect` before any other tool.\n"
        "- Use `t32_cmd` as a fallback for any TRACE32 command not covered by specific tools.\n"
        "- Never generate Python/shell scripts to interact with TRACE32. Use the tools directly.\n"
    ),
    "trace32-multicore-workflow": (
        "# TRACE32 Multi-Core Debugging Workflow\n"
        "\n"
        "You have direct access to up to 16 TRACE32 cores via MCP tools.\n"
        "\n"
        "## Connecting\n"
        "- **Single core**: `t32_connect` with host, port, core_id\n"
        "- **All cores at once**: `t32_connect_all` with host, base_port, num_cores\n"
        "  (connects to consecutive ports: base_port, base_port+1, ...)\n"
        "\n"
        "## Managing cores\n"
        "- `t32_list_cores` — see which cores are connected\n"
        "- All tools accept `core_id` (0-15, default 0) to target a specific core\n"
        "- `t32_set_endian` / `t32_get_endian` — per-core endianness\n"
        "\n"
        "## Example: Read PC from all 4 cores\n"
        "Call `t32_read_register` four times with name='PC' and core_id=0,1,2,3.\n"
        "Do NOT write a loop script. Call the tool directly for each core.\n"
        "\n"
        "## Disconnecting\n"
        "- `t32_disconnect` — single core\n"
        "- `t32_disconnect_all` — all cores at once\n"
    ),
}

RESOURCES = [
    {
        "uri": "trace32://instructions",
        "name": "TRACE32 MCP Usage Instructions",
        "description": (
            "How to use the TRACE32 MCP tools. Read this first before "
            "interacting with TRACE32. Explains available tools and usage rules."
        ),
        "mimeType": "text/plain"
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "trace32://core/{core_id}/status",
        "name": "Core Status",
        "description": "Get connection status and endianness for a specific core (0-15).",
        "mimeType": "application/json"
    },
]

_RESOURCE_CONTENTS = {
    "trace32://instructions": (
        "# TRACE32 MCP Server - Usage Instructions\n"
        "\n"
        "You are connected to a TRACE32 MCP server that provides direct tool access "
        "to TRACE32 PowerView debugger instances.\n"
        "\n"
        "## CRITICAL RULES\n"
        "1. **Use the MCP tools directly.** Do NOT generate Python scripts, shell commands, "
        "or HTTP API calls to interact with TRACE32.\n"
        "2. **Do NOT import or reference** `t32.client`, `http_server`, or any TRACE32 library. "
        "The MCP tools handle everything.\n"
        "3. **Always connect first.** Call `t32_connect` (or `t32_connect_all`) before "
        "using any other tool.\n"
        "\n"
        "## Available Tools (28)\n"
        "\n"
        "### Connection\n"
        "- `t32_connect` — Connect to a TRACE32 instance\n"
        "- `t32_connect_all` — Connect to multiple cores at once\n"
        "- `t32_disconnect` / `t32_disconnect_all` — Disconnect\n"
        "- `t32_list_cores` — List connected cores\n"
        "\n"
        "### Configuration\n"
        "- `t32_set_endian` / `t32_get_endian` — Per-core endianness\n"
        "\n"
        "### Execution Control\n"
        "- `t32_go` — Resume execution\n"
        "- `t32_break` — Halt execution\n"
        "- `t32_step` — Single step (with step-over option)\n"
        "- `t32_get_state` — Get CPU state\n"
        "\n"
        "### Memory & Registers\n"
        "- `t32_read_memory` / `t32_write_memory` — Memory access\n"
        "- `t32_read_register` / `t32_write_register` — Register access\n"
        "- `t32_read_variable` / `t32_write_variable` — C/C++ variable access\n"
        "\n"
        "### Debug\n"
        "- `t32_breakpoint_set` / `t32_breakpoint_delete` / `t32_breakpoint_list`\n"
        "- `t32_get_symbol` — Get symbol address\n"
        "\n"
        "### Commands\n"
        "- `t32_cmd` — Execute any PRACTICE command\n"
        "- `t32_eval` — Evaluate a TRACE32 expression\n"
        "- `t32_run_script` — Run a .cmm script\n"
        "- `t32_load` — Load ELF/binary to target\n"
        "- `t32_memory_dump` — Dump memory to file (binary or T32-style text)\n"
        "- `t32_memory_load` — Load file to target memory (binary or T32-style text)\n"
        "- `t32_get_version` — Get TRACE32 version\n"
        "\n"
        "## Multi-Core\n"
        "All tools accept `core_id` (0-15, default 0). Each core maps to a separate "
        "TRACE32 PowerView instance on a different port.\n"
    ),
}


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
    _send_log("info", "Connecting to {0}:{1} (core {2})".format(host, port, core_id),
              logger="trace32.connect")
    client = _core_manager.connect_core(core_id, host, port)
    version = ""
    try:
        version = client.get_version()
    except Exception:
        pass
    result = {"status": "connected", "core_id": core_id, "host": host, "port": port}
    if version:
        result["version"] = version
    _send_log("info", "Connected to core {0} ({1}:{2})".format(core_id, host, port),
              logger="trace32.connect")
    return result


def _handle_connect_all(args, progress_token=None, request_id=None):
    host = args.get("host", "localhost")
    base_port = int(args.get("base_port", 20000))
    num_cores = int(args.get("num_cores", 16))
    _send_log("info", "Connecting {0} cores starting at {1}:{2}".format(
        num_cores, host, base_port), logger="trace32.connect")
    _send_progress(progress_token, 0, total=num_cores,
                   message="Starting multi-core connect")

    results = []
    cancelled = False
    for i in range(num_cores):
        if request_id is not None and _is_cancelled(request_id):
            _send_log("info", "connect_all cancelled at core {0}/{1}".format(
                i, num_cores), logger="trace32.cancel")
            cancelled = True
            break
        core_id = i
        port = base_port + i
        try:
            _core_manager.connect_core(core_id, host, port)
            results.append({"core_id": core_id, "status": "connected",
                            "host": host, "port": port})
        except Exception as e:
            results.append({"core_id": core_id, "status": "failed",
                            "error": str(e)})
        _send_progress(progress_token, i + 1, total=num_cores,
                       message="Connected core {0}/{1}".format(i + 1, num_cores))

    connected = sum(1 for r in results if r["status"] == "connected")
    _send_log("info", "Connected {0}/{1} cores".format(connected, num_cores),
              logger="trace32.connect")
    result = {
        "total": num_cores,
        "connected": connected,
        "failed": num_cores - connected,
        "cores": results,
    }
    if cancelled:
        result["cancelled"] = True
    return result


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


def _format_hex_dump(data, start_address, access="D", bytes_per_line=16):
    """Format raw bytes as T32-style hex dump text.

    Output example:
        D:0x00001000: DE AD BE EF CA FE BA BE 01 02 03 04 05 06 07 08  |........ABCDEFGH|
    """
    lines = []
    for offset in range(0, len(data), bytes_per_line):
        chunk = data[offset:offset + bytes_per_line]
        addr = start_address + offset
        hex_part = " ".join("{0:02X}".format(b if isinstance(b, int) else ord(b))
                            for b in chunk)
        # Pad hex part to fixed width
        hex_width = bytes_per_line * 3 - 1
        hex_part = hex_part.ljust(hex_width)
        ascii_part = ""
        for b in chunk:
            c = b if isinstance(b, int) else ord(b)
            ascii_part += chr(c) if 0x20 <= c <= 0x7E else "."
        lines.append("{0}:0x{1:08X}: {2}  |{3}|".format(
            access, addr, hex_part, ascii_part))
    return "\n".join(lines) + "\n"


def _parse_hex_dump(text):
    """Parse T32-style hex dump text back to raw bytes.

    Accepts lines like:
        D:0x00001000: DE AD BE EF ...  |....|
    Returns:
        (start_address, bytes_data) tuple.
    """
    result = bytearray()
    first_addr = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Find the address:hex part — skip access prefix
        colon_idx = line.find(":0x")
        if colon_idx < 0:
            continue
        rest = line[colon_idx + 1:]  # "0x00001000: DE AD ..."
        parts = rest.split(":", 1)
        if len(parts) < 2:
            continue
        addr_str = parts[0].strip()
        hex_and_ascii = parts[1].strip()
        if first_addr is None:
            first_addr = int(addr_str, 16)
        # Strip ASCII part (after |)
        pipe_idx = hex_and_ascii.find("|")
        if pipe_idx >= 0:
            hex_and_ascii = hex_and_ascii[:pipe_idx].strip()
        # Parse hex bytes
        for token in hex_and_ascii.split():
            if len(token) == 2:
                try:
                    result.append(int(token, 16))
                except ValueError:
                    break
    return (first_addr if first_addr is not None else 0, bytes(result))


def _resolve_address(address):
    """Parse address string, return (int_address, access_prefix_or_None).

    Examples: '0x1000' -> (0x1000, None), 'D:0x1000' -> (0x1000, 'D')
    """
    if isinstance(address, int):
        return address, None
    addr_str = str(address)
    access_prefix = None
    if ":" in addr_str and not addr_str.startswith("0x"):
        parts = addr_str.split(":", 1)
        access_prefix = parts[0]
        addr_str = parts[1]
    return int(addr_str, 0), access_prefix


def _handle_memory_dump(args):
    client = _get_client(args)
    core_id = int(args.get("core_id", 0))
    address = args["address"]
    size = int(args["size"])
    path = args["path"]
    access = args.get("access", "D")
    fmt = args.get("format", "bin")

    int_addr, addr_access = _resolve_address(address)
    if addr_access:
        access = addr_access

    raw_data = client.read_memory(int_addr, size, access)

    if fmt == "text":
        content = _format_hex_dump(raw_data, int_addr, access)
        with open(path, "w") as f:
            f.write(content)
    else:
        with open(path, "wb") as f:
            f.write(raw_data)

    return {
        "status": "ok",
        "address": "0x{0:X}".format(int_addr),
        "size": len(raw_data),
        "path": path,
        "format": fmt,
    }


def _handle_memory_load(args):
    client = _get_client(args)
    address = args["address"]
    path = args["path"]
    access = args.get("access", "D")
    fmt = args.get("format", "bin")

    int_addr, addr_access = _resolve_address(address)
    if addr_access:
        access = addr_access

    if fmt == "text":
        with open(path, "r") as f:
            text = f.read()
        file_addr, data = _parse_hex_dump(text)
        # Use file's address if user didn't provide explicit address
        if isinstance(args["address"], str) and args["address"] == "auto":
            int_addr = file_addr
    else:
        with open(path, "rb") as f:
            data = f.read()

    hex_data = binascii.hexlify(data).decode("ascii")
    client.write_memory(int_addr, hex_data, access)

    return {
        "status": "ok",
        "address": "0x{0:X}".format(int_addr),
        "bytes_written": len(data),
        "path": path,
        "format": fmt,
    }


def _find_t32start(explicit_path=None):
    """Locate t32start.exe: explicit path > T32_START env > T32SYS env > PATH."""
    if explicit_path:
        if os.path.isfile(explicit_path):
            return explicit_path
        raise Trace32Error(
            "t32start executable not found: {0}".format(explicit_path))

    # Environment variable: T32_START (direct path to executable)
    env_start = os.environ.get("T32_START", "")
    if env_start and os.path.isfile(env_start):
        return env_start

    # Environment variable: T32SYS (TRACE32 installation directory)
    t32sys = os.environ.get("T32SYS", "")
    if t32sys:
        for subdir in ("bin/windows64", "bin/windows", "bin"):
            candidate = os.path.join(t32sys, subdir, "t32start.exe")
            if os.path.isfile(candidate):
                return candidate

    # Fallback: assume it's on PATH
    return "t32start.exe"


def _handle_start(args):
    executable = _find_t32start(args.get("executable"))

    cmd_line = [executable]

    # runcfg: parameter > T32_RUNCFG env
    runcfg = args.get("runcfg") or os.environ.get("T32_RUNCFG", "")

    # Named options: -flag value
    for flag, val in (("-runcfg", runcfg),
                      ("-runitem", args.get("runitem")),
                      ("-runaliases", args.get("runaliases"))):
        if val:
            cmd_line.extend([flag, val])

    # Additional arbitrary arguments
    extra = args.get("args")
    if extra:
        cmd_line.extend(extra)

    wait = args.get("wait", False)
    timeout = float(args.get("timeout", 30))

    try:
        proc = subprocess.Popen(
            cmd_line,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as e:
        raise Trace32Error(
            "Failed to launch t32start: {0}".format(e))

    result = {
        "status": "launched",
        "pid": proc.pid,
        "command": cmd_line,
    }

    if wait:
        deadline = time.time() + timeout
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.5)
        if proc.poll() is None:
            result["status"] = "timeout"
            result["message"] = (
                "t32start still running after {0}s".format(int(timeout)))
        else:
            result["status"] = "exited"
            result["returncode"] = proc.returncode
            stdout = proc.stdout.read().decode("ascii", errors="replace").strip()
            stderr = proc.stderr.read().decode("ascii", errors="replace").strip()
            if stdout:
                result["stdout"] = stdout
            if stderr:
                result["stderr"] = stderr

    return result


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
    "t32_memory_dump": _handle_memory_dump,
    "t32_memory_load": _handle_memory_load,
    "t32_start": _handle_start,
}

# Tools that accept progress_token kwarg
_PROGRESS_HANDLERS = {"t32_connect_all"}


# ======================================================================
# MCP Protocol Handler
# ======================================================================

def _make_response(req_id, result):
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id, code, message):
    """Build a JSON-RPC 2.0 error response."""
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ======================================================================
# MCP Logging — notifications/message
# ======================================================================

# Valid MCP log levels (RFC 5424 subset)
LOG_LEVELS = ["debug", "info", "notice", "warning", "error", "critical",
              "alert", "emergency"]

# Output function for sending notifications. Replaced by _write_message
# in main(), injectable for testing.
_notification_sink = None


def _send_log(level, message, logger=None, data=None):
    """Send a logging notification to the MCP client.

    Args:
        level: Log level (debug, info, warning, error, etc.)
        message: Human-readable log message.
        logger: Optional logger name (e.g. 'trace32.connect').
        data: Optional structured data (JSON-serializable).
    """
    if _notification_sink is None:
        return
    if level not in LOG_LEVELS:
        level = "info"
    params = {"level": level, "message": message}
    if logger is not None:
        params["logger"] = logger
    if data is not None:
        params["data"] = data
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": params
    }
    try:
        _notification_sink(notification)
    except Exception:
        pass


def _send_progress(progress_token, progress, total=None, message=None):
    """Send a progress notification to the MCP client.

    Args:
        progress_token: Token from the client's _meta.progressToken.
        progress: Current progress value.
        total: Optional total value for percentage calculation.
        message: Optional human-readable status message.
    """
    if _notification_sink is None or progress_token is None:
        return
    params = {"progressToken": progress_token, "progress": progress}
    if total is not None:
        params["total"] = total
    if message is not None:
        params["message"] = message
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": params
    }
    try:
        _notification_sink(notification)
    except Exception:
        pass


import re as _re
import threading as _threading

_CORE_STATUS_RE = _re.compile(r'^trace32://core/(\d+)/status$')

# Cancellation tracking
_cancelled_requests = set()
_cancelled_lock = _threading.Lock()


def _cancel_request(request_id):
    """Mark a request as cancelled."""
    with _cancelled_lock:
        _cancelled_requests.add(request_id)


def _is_cancelled(request_id):
    """Check if a request has been cancelled."""
    with _cancelled_lock:
        return request_id in _cancelled_requests


def _clear_cancelled(request_id):
    """Remove a request from the cancelled set."""
    with _cancelled_lock:
        _cancelled_requests.discard(request_id)


def _resolve_resource_template(uri):
    """Resolve a dynamic resource URI template. Returns content dict or None."""
    m = _CORE_STATUS_RE.match(uri)
    if m:
        core_id = int(m.group(1))
        if core_id < 0 or core_id > 15:
            return None
        cores = _core_manager.list_cores()
        core_info = None
        for c in cores:
            if c.get("core_id") == core_id:
                core_info = c
                break
        if core_info is None:
            core_info = {"core_id": core_id, "connected": False}
        text = json.dumps(core_info, indent=2, ensure_ascii=False)
        return {"uri": uri, "mimeType": "application/json", "text": text}
    return None


def _handle_completion(ref, argument):
    """Handle completion/complete requests.

    Args:
        ref: Reference object with type and name/uri.
        argument: Argument object with name and value (partial input).

    Returns:
        Completion result dict with values list and hasMore/total.
    """
    ref_type = ref.get("type", "")
    value = argument.get("value", "")

    if ref_type == "ref/prompt":
        # Complete prompt names
        all_names = [p["name"] for p in PROMPTS]
        matches = [n for n in all_names if n.startswith(value)]
        return {"values": matches, "total": len(matches), "hasMore": False}

    elif ref_type == "ref/resource":
        uri = ref.get("uri", "")
        # Complete resource template URIs
        if uri == "trace32://core/{core_id}/status" or \
                uri.startswith("trace32://core/"):
            # Suggest core IDs 0-15
            prefix = value
            core_ids = [str(i) for i in range(16)]
            matches = [c for c in core_ids if c.startswith(prefix)]
            return {"values": matches, "total": len(matches), "hasMore": False}
        # Complete static resource URIs
        all_uris = [r["uri"] for r in RESOURCES]
        matches = [u for u in all_uris if u.startswith(value)]
        return {"values": matches, "total": len(matches), "hasMore": False}

    return {"values": [], "total": 0, "hasMore": False}


def _handle_request(request):
    """Process a single JSON-RPC request and return a response dict (or None)."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    # Notifications (no id) don't get responses
    if req_id is None:
        if method == "notifications/cancelled":
            cancelled_id = params.get("requestId")
            if cancelled_id is not None:
                _cancel_request(cancelled_id)
                _send_log("info", "Request {0} cancelled by client".format(
                    cancelled_id), logger="trace32.cancel")
        return None

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "prompts": {},
                "resources": {},
                "logging": {}
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
        meta = tool_args.pop("_meta", params.get("_meta", {})) or {}
        progress_token = meta.get("progressToken")

        handler = _HANDLERS.get(tool_name)
        if handler is None:
            _send_log("warning", "Unknown tool called: " + tool_name,
                      logger="trace32.tools")
            return _make_response(req_id, {
                "content": [{"type": "text", "text": "Unknown tool: " + tool_name}],
                "isError": True
            })

        _send_log("debug", "Calling tool: " + tool_name,
                  logger="trace32.tools", data=tool_args)
        try:
            # Pass progress_token and request_id to handlers that support it
            if tool_name in _PROGRESS_HANDLERS:
                result = handler(tool_args, progress_token=progress_token,
                                 request_id=req_id)
            else:
                result = handler(tool_args)
            _clear_cancelled(req_id)
            text = json.dumps(result, indent=2, ensure_ascii=False)
            _send_log("debug", "Tool completed: " + tool_name,
                      logger="trace32.tools")
            return _make_response(req_id, {
                "content": [{"type": "text", "text": text}]
            })
        except Trace32Error as e:
            _send_log("error", "TRACE32 error in {0}: {1}".format(
                tool_name, str(e)), logger="trace32.tools")
            return _make_response(req_id, {
                "content": [{"type": "text", "text": "TRACE32 Error: " + str(e)}],
                "isError": True
            })
        except Exception as e:
            _send_log("error", "Error in {0}: {1}".format(
                tool_name, str(e)), logger="trace32.tools")
            return _make_response(req_id, {
                "content": [{"type": "text", "text": "Error: " + str(e)}],
                "isError": True
            })

    elif method == "resources/list":
        return _make_response(req_id, {
            "resources": RESOURCES,
            "resourceTemplates": RESOURCE_TEMPLATES
        })

    elif method == "resources/read":
        uri = params.get("uri", "")
        # Static resources
        content = _RESOURCE_CONTENTS.get(uri)
        if content is not None:
            return _make_response(req_id, {
                "contents": [{"uri": uri, "mimeType": "text/plain", "text": content}]
            })
        # Dynamic resource templates
        result = _resolve_resource_template(uri)
        if result is not None:
            return _make_response(req_id, {"contents": [result]})
        return _make_error(req_id, -32602, "Unknown resource: " + uri)

    elif method == "prompts/list":
        return _make_response(req_id, {"prompts": PROMPTS})

    elif method == "prompts/get":
        prompt_name = params.get("name", "")
        content = _PROMPT_CONTENTS.get(prompt_name)
        if content is None:
            return _make_error(req_id, -32602, "Unknown prompt: " + prompt_name)
        # Find description from PROMPTS list
        description = ""
        for p in PROMPTS:
            if p["name"] == prompt_name:
                description = p.get("description", "")
                break
        return _make_response(req_id, {
            "description": description,
            "messages": [
                {"role": "user", "content": {"type": "text", "text": content}}
            ]
        })

    elif method == "completion/complete":
        ref = params.get("ref", {})
        argument = params.get("argument", {})
        completions = _handle_completion(ref, argument)
        return _make_response(req_id, {"completion": completions})

    else:
        return _make_error(req_id, -32601, "Method not found: " + method)


def _write_message(msg):
    """Write a JSON-RPC message to stdout (newline-delimited)."""
    line = json.dumps(msg, ensure_ascii=True)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main():
    """Main MCP server loop. Reads from stdin, writes to stdout."""
    global _notification_sink
    _notification_sink = _write_message

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
