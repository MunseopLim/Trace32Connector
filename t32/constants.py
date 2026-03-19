#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 Remote Control Protocol Constants.

Based on the TRACE32 Remote API (RCL) protocol documentation and
reference implementation (hremote.c / hlinknet.c).
"""
from __future__ import print_function

# ============================================================
# RCL Command IDs (byte 0 of message)
# ============================================================
CMD_NOP = 0x70               # No-operation / keep-alive
CMD_ATTACH = 0x71            # Attach to device
CMD_EXECUTE_PRACTICE = 0x72  # Execute PRACTICE command string
CMD_PING = 0x73              # Ping / connectivity check
CMD_DEVICE_SPECIFIC = 0x74   # Device-specific operations
CMD_CMDWINDOW = 0x75         # Command with window context
CMD_GETMSG = 0x76            # Get message from TRACE32
CMD_EDITNOTIFY = 0x78        # Notification configuration
CMD_TERMINATE = 0x79         # Terminate TRACE32

# ============================================================
# Device-Specific Sub-Commands (byte 1 when CMD=0x74)
# ============================================================
SUBCMD_GET_STATE = 0x10          # Get target run state
SUBCMD_RESET_CPU = 0x11          # Reset CPU
SUBCMD_GET_CPU_INFO = 0x13       # Get CPU information
SUBCMD_GET_MEMORY_MAP = 0x16     # Get memory mapping
SUBCMD_READ_REGISTER = 0x20      # Read registers (by mask)
SUBCMD_WRITE_REGISTER = 0x21     # Write registers (by mask)
SUBCMD_READ_PP = 0x22            # Read program counter
SUBCMD_READ_REG_BY_NAME = 0x23   # Read register by name
SUBCMD_WRITE_REG_BY_NAME = 0x24  # Write register by name
SUBCMD_READ_MEMORY = 0x30        # Read target memory
SUBCMD_WRITE_MEMORY = 0x31       # Write target memory
SUBCMD_READ_BP = 0x40            # Read breakpoint info
SUBCMD_SET_BP = 0x41             # Set breakpoint
SUBCMD_DELETE_BP = 0x42          # Delete breakpoint
SUBCMD_STEP = 0x50               # Single step
SUBCMD_GO = 0x51                 # Go (start execution)
SUBCMD_BREAK = 0x52              # Break (halt execution)
SUBCMD_GET_BP_LIST = 0x64        # Get breakpoint list
SUBCMD_EVAL_GET = 0x0E           # Get eval result (numeric)
SUBCMD_EVAL_GET_STRING = 0x0F    # Get eval result (string)

# ============================================================
# Device Types (for ATTACH command)
# ============================================================
DEV_OS = 0x00       # Operating system awareness
DEV_ICD = 0x01      # In-Circuit Debugger (main debug device)
DEV_ICE = 0x02      # In-Circuit Emulator
DEV_FIRE = 0x03     # Firewire device

# ============================================================
# Target CPU States (returned by GET_STATE)
# ============================================================
STATE_DOWN = 0x00       # System down (not connected)
STATE_HALTED = 0x01     # System halted (powered but no debug)
STATE_STOPPED = 0x02    # Stopped (at breakpoint / halted by user)
STATE_RUNNING = 0x03    # Target is running

STATE_NAMES = {
    STATE_DOWN: 'down',
    STATE_HALTED: 'halted',
    STATE_STOPPED: 'stopped',
    STATE_RUNNING: 'running',
}

# ============================================================
# Memory Access Classes
# ============================================================
ACCESS_DATA = 0x00          # D: Data access
ACCESS_PROGRAM = 0x01       # P: Program access
ACCESS_DATA_NC = 0x02       # NC: Non-cacheable data
ACCESS_SUPERVISOR_DATA = 0x0F  # SD: Supervisor data
ACCESS_SUPERVISOR_PROG = 0x10  # SP: Supervisor program

ACCESS_CLASSES = {
    'D': ACCESS_DATA,
    'P': ACCESS_PROGRAM,
    'NC': ACCESS_DATA_NC,
    'SD': ACCESS_SUPERVISOR_DATA,
    'SP': ACCESS_SUPERVISOR_PROG,
    'DATA': ACCESS_DATA,
    'PROGRAM': ACCESS_PROGRAM,
}

# ============================================================
# Error Codes
# ============================================================
ERR_OK = 0x00
ERR_RECEIVE = 0x01           # Receive error
ERR_TRANSMIT = 0x02          # Transmit error
ERR_ATTACH = 0x03            # Attach error
ERR_CMD_FAILED = 0x04        # Command execution failed
ERR_INVALID_STATE = 0x10     # Invalid target state
ERR_NOT_CONNECTED = 0x20     # Not connected

ERROR_NAMES = {
    ERR_OK: 'OK',
    ERR_RECEIVE: 'Receive error',
    ERR_TRANSMIT: 'Transmit error',
    ERR_ATTACH: 'Attach error',
    ERR_CMD_FAILED: 'Command failed',
    ERR_INVALID_STATE: 'Invalid target state',
    ERR_NOT_CONNECTED: 'Not connected',
}

# ============================================================
# Breakpoint Types
# ============================================================
BP_PROGRAM = 0x01       # Program (execution) breakpoint
BP_READ = 0x02          # Read watchpoint
BP_WRITE = 0x04         # Write watchpoint
BP_READWRITE = 0x06     # Read/Write watchpoint

BP_TYPES = {
    'program': BP_PROGRAM,
    'read': BP_READ,
    'write': BP_WRITE,
    'readwrite': BP_READWRITE,
    'rw': BP_READWRITE,
}

# ============================================================
# Protocol Defaults
# ============================================================
DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 20000
DEFAULT_TIMEOUT = 10.0
DEFAULT_PACKLEN_TCP = 16384
DEFAULT_PACKLEN_UDP = 1024

# ============================================================
# Multi-Core Defaults
# ============================================================
MAX_CORES = 16
DEFAULT_BASE_PORT = 20000
