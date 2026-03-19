#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 Remote API Client.

Pure Python TCP client for communicating with TRACE32 PowerView
via the Remote Control (RCL/NETTCP) protocol.

Compatible with Python 2.7 and 3.4+.
No external dependencies.

TRACE32 PowerView setup (config.t32):
    RCL=NETTCP
    PORT=20000

Usage:
    from t32.client import Trace32Client
    client = Trace32Client()
    client.connect('localhost', 20000)
    client.cmd('SYStem.Up')
    state = client.get_state()
    client.disconnect()
"""
from __future__ import print_function

import socket
import struct
import sys
import time
import binascii

from .constants import (
    CMD_NOP, CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING,
    CMD_DEVICE_SPECIFIC, CMD_GETMSG, CMD_TERMINATE,
    SUBCMD_GET_STATE, SUBCMD_READ_PP, SUBCMD_READ_REG_BY_NAME,
    SUBCMD_WRITE_REG_BY_NAME, SUBCMD_READ_MEMORY, SUBCMD_WRITE_MEMORY,
    SUBCMD_EVAL_GET_STRING,
    DEV_ICD,
    STATE_NAMES,
    ACCESS_CLASSES, ACCESS_DATA,
    ERR_OK, ERROR_NAMES,
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT, DEFAULT_PACKLEN_TCP,
)

PY2 = sys.version_info[0] == 2


def _to_bytes(s):
    """Convert string to bytes (ASCII). Works in Python 2.7 and 3.4+."""
    if isinstance(s, bytes):
        return s
    return s.encode('ascii')


def _parse_address(addr_input):
    """Parse address from various input formats.

    Returns (address_int, access_class_str_or_None).

    Examples:
        0x1000        -> (4096, None)
        "0x1000"      -> (4096, None)
        "D:0x1000"    -> (4096, 'D')
        "P:0x2000"    -> (8192, 'P')
    """
    if isinstance(addr_input, int):
        return (addr_input, None)

    addr_str = str(addr_input).strip()

    if ':' in addr_str:
        parts = addr_str.split(':', 1)
        prefix = parts[0].strip().upper()
        if prefix in ACCESS_CLASSES:
            addr = int(parts[1].strip(), 0)
            return (addr, prefix)

    return (int(addr_str, 0), None)


class Trace32Error(Exception):
    """Exception raised for TRACE32 communication or command errors."""

    def __init__(self, message, error_code=None):
        self.error_code = error_code
        if error_code is not None:
            err_name = ERROR_NAMES.get(error_code, 'Unknown')
            message = "{0} (code=0x{1:02X} {2})".format(message, error_code, err_name)
        super(Trace32Error, self).__init__(message)


class Trace32Client(object):
    """TRACE32 Remote API client over TCP (NETTCP protocol).

    Implements the binary protocol for direct communication with
    TRACE32 PowerView. All high-level debugging operations are
    available as methods.
    """

    def __init__(self):
        self._sock = None
        self._msg_id = 0
        self._connected = False
        self._host = None
        self._port = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @property
    def connected(self):
        """True if connected to TRACE32."""
        return self._connected

    def connect(self, host=DEFAULT_HOST, port=DEFAULT_PORT,
                timeout=DEFAULT_TIMEOUT, device=DEV_ICD):
        """Connect to a running TRACE32 PowerView instance.

        Args:
            host: Hostname or IP address (default: localhost)
            port: RCL port number (default: 20000)
            timeout: Socket timeout in seconds (default: 10.0)
            device: Device type (default: DEV_ICD)

        Raises:
            Trace32Error: If connection or attach fails.
        """
        if self._connected:
            self.disconnect()

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.connect((host, port))
        except socket.error as e:
            self._sock = None
            raise Trace32Error(
                "Cannot connect to TRACE32 at {0}:{1} - {2}. "
                "Ensure PowerView is running with RCL=NETTCP and PORT={1} "
                "in config.t32".format(host, port, e)
            )

        # Send ATTACH command
        try:
            msg = self._build_msg(CMD_ATTACH, device)
            self._send(msg)
            resp = self._recv()
            self._check_response(resp, CMD_ATTACH)
        except Exception as e:
            self._sock.close()
            self._sock = None
            raise Trace32Error("ATTACH failed: {0}".format(e))

        self._connected = True
        self._host = host
        self._port = port

    def disconnect(self):
        """Disconnect from TRACE32."""
        if self._sock:
            try:
                msg = self._build_msg(CMD_NOP, 0)
                self._send(msg)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._connected = False

    def ping(self):
        """Ping TRACE32 to check connection is alive.

        Returns:
            True if TRACE32 responds.

        Raises:
            Trace32Error: If ping fails.
        """
        self._ensure_connected()
        msg = self._build_msg(CMD_PING, 0)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_PING)
        return True

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def cmd(self, command):
        """Execute a TRACE32 PRACTICE command string.

        This is the most versatile method - any command you can type
        in the TRACE32 command line can be used here.

        Args:
            command: TRACE32 command string (e.g. "SYStem.Up",
                     "Break.Set 0x1000", "Data.dump 0x0--0xFF")

        Returns:
            True on success.

        Raises:
            Trace32Error: If command fails.
        """
        self._ensure_connected()
        cmd_bytes = _to_bytes(command)
        payload = bytearray(cmd_bytes) + bytearray([0x00])  # null-terminated
        msg = self._build_msg(CMD_EXECUTE_PRACTICE, 0x00, payload)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_EXECUTE_PRACTICE)
        return True

    def cmd_with_result(self, command):
        """Execute a TRACE32 command and return any text result.

        Sends the command, then retrieves the message from the AREA window.
        Useful for commands like PRINT that produce text output.

        Args:
            command: TRACE32 command string.

        Returns:
            Result text string.
        """
        self.cmd(command)
        return self.get_message()['text']

    def eval_expression(self, expression):
        """Evaluate a TRACE32 expression and return the text result.

        Uses PRINT to output the expression result to the AREA window,
        then retrieves it.

        Args:
            expression: TRACE32 expression (e.g. "Register(PC)",
                        "Var.VALUE(myVar)", "VERSION.SOFTWARE()")

        Returns:
            Result as a string.
        """
        self.cmd("PRINT " + (expression if isinstance(expression, str) else expression.decode('ascii')))
        return self.get_message()['text']

    def get_message(self):
        """Get the last message from the TRACE32 AREA window.

        Returns:
            Dict with 'mode' (int) and 'text' (str).
        """
        self._ensure_connected()
        msg = self._build_msg(CMD_GETMSG, 0x00)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_GETMSG)

        mode = 0
        text = ''
        if len(resp) > 5:
            mode = struct.unpack_from('<H', bytes(resp[3:5]))[0]
            text_data = resp[5:]
            # Find null terminator
            null_pos = len(text_data)
            for i in range(len(text_data)):
                if text_data[i] == 0:
                    null_pos = i
                    break
            text = bytes(text_data[:null_pos]).decode('ascii', errors='replace')

        return {'mode': mode, 'text': text}

    # ------------------------------------------------------------------
    # Target state and control
    # ------------------------------------------------------------------

    def get_state(self):
        """Get the current target CPU state.

        Returns:
            Dict with 'state_code' (int) and 'state_name' (str).
            Possible states: 'down', 'halted', 'stopped', 'running'.
        """
        self._ensure_connected()
        msg = self._build_msg(CMD_DEVICE_SPECIFIC, SUBCMD_GET_STATE)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_DEVICE_SPECIFIC)

        state_code = resp[3] if len(resp) > 3 else 0xFF
        return {
            'state_code': state_code,
            'state_name': STATE_NAMES.get(state_code, 'unknown'),
        }

    def go(self):
        """Start target CPU execution."""
        return self.cmd("Go")

    def break_target(self):
        """Halt (break) target CPU execution."""
        return self.cmd("Break")

    def step(self, count=1):
        """Single-step the target CPU.

        Args:
            count: Number of steps (default 1).
        """
        if count == 1:
            return self.cmd("Step")
        return self.cmd("Step {0}".format(count))

    def step_over(self):
        """Step over (function call)."""
        return self.cmd("Step.Over")

    def system_up(self):
        """Connect debugger to target (SYStem.Up)."""
        return self.cmd("SYStem.Up")

    def system_down(self):
        """Disconnect debugger from target (SYStem.Down)."""
        return self.cmd("SYStem.Down")

    def reset_target(self):
        """Reset target CPU."""
        return self.cmd("SYStem.RESetTarget")

    # ------------------------------------------------------------------
    # Memory access
    # ------------------------------------------------------------------

    def read_memory(self, address, size, access='D'):
        """Read target memory (binary protocol).

        Args:
            address: Memory address (int or string like "D:0x1000")
            size: Number of bytes to read (max ~16000 per call)
            access: Access class ('D', 'P', 'SD', etc.) or None

        Returns:
            bytes object with memory content.

        Raises:
            Trace32Error: On read failure.
        """
        self._ensure_connected()
        addr_int, parsed_access = _parse_address(address)
        if parsed_access:
            access = parsed_access
        access_code = ACCESS_CLASSES.get(access, ACCESS_DATA) if isinstance(access, str) else access

        payload = bytearray()
        payload.extend(struct.pack('<I', addr_int))
        payload.extend(struct.pack('<H', size))
        payload.append(access_code)

        msg = self._build_msg(CMD_DEVICE_SPECIFIC, SUBCMD_READ_MEMORY, payload)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_DEVICE_SPECIFIC)

        return bytes(resp[3:3 + size])

    def read_memory_hex(self, address, size, access='D'):
        """Read target memory and return as hex string.

        Args:
            address: Memory address
            size: Number of bytes
            access: Access class

        Returns:
            Hex string (e.g. "DEADBEEF01020304")
        """
        data = self.read_memory(address, size, access)
        return binascii.hexlify(data).decode('ascii').upper()

    def write_memory(self, address, data, access='D'):
        """Write data to target memory (binary protocol).

        Args:
            address: Memory address (int or string)
            data: bytes/bytearray to write, or hex string
            access: Access class

        Returns:
            True on success.
        """
        self._ensure_connected()
        addr_int, parsed_access = _parse_address(address)
        if parsed_access:
            access = parsed_access
        access_code = ACCESS_CLASSES.get(access, ACCESS_DATA) if isinstance(access, str) else access

        if isinstance(data, str):
            data = binascii.unhexlify(data.replace(' ', ''))
        elif not isinstance(data, (bytes, bytearray)):
            raise Trace32Error("data must be bytes, bytearray, or hex string")

        payload = bytearray()
        payload.extend(struct.pack('<I', addr_int))
        payload.extend(struct.pack('<H', len(data)))
        payload.append(access_code)
        payload.extend(data)

        msg = self._build_msg(CMD_DEVICE_SPECIFIC, SUBCMD_WRITE_MEMORY, payload)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_DEVICE_SPECIFIC)
        return True

    # ------------------------------------------------------------------
    # Register access
    # ------------------------------------------------------------------

    def read_register(self, name):
        """Read a CPU register by name (binary protocol).

        Args:
            name: Register name (e.g. 'PC', 'R0', 'SP', 'CPSR')

        Returns:
            Register value as integer (up to 64-bit).
        """
        self._ensure_connected()
        name_bytes = _to_bytes(name)
        payload = bytearray(name_bytes) + bytearray([0x00])

        msg = self._build_msg(CMD_DEVICE_SPECIFIC, SUBCMD_READ_REG_BY_NAME, payload)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_DEVICE_SPECIFIC)

        if len(resp) >= 11:
            value_lo = struct.unpack_from('<I', bytes(resp[3:7]))[0]
            value_hi = struct.unpack_from('<I', bytes(resp[7:11]))[0]
            return (value_hi << 32) | value_lo
        elif len(resp) >= 7:
            return struct.unpack_from('<I', bytes(resp[3:7]))[0]
        return 0

    def write_register(self, name, value):
        """Write a CPU register by name.

        Uses PRACTICE command for reliability.

        Args:
            name: Register name
            value: Integer value to write
        """
        return self.cmd("Register.Set {0} 0x{1:X}".format(name, value))

    def read_pc(self):
        """Read program counter (PP) via binary protocol.

        Returns:
            Program counter value as integer.
        """
        self._ensure_connected()
        msg = self._build_msg(CMD_DEVICE_SPECIFIC, SUBCMD_READ_PP)
        self._send(msg)
        resp = self._recv()
        self._check_response(resp, CMD_DEVICE_SPECIFIC)

        if len(resp) >= 7:
            return struct.unpack_from('<I', bytes(resp[3:7]))[0]
        return 0

    # ------------------------------------------------------------------
    # Breakpoints
    # ------------------------------------------------------------------

    def set_breakpoint(self, address, bp_type='program', size=None):
        """Set a breakpoint.

        Args:
            address: Target address (int or string)
            bp_type: 'program', 'read', 'write', 'readwrite'
            size: Optional size for data breakpoints
        """
        addr_int, _ = _parse_address(address)
        cmd_str = "Break.Set 0x{0:X}".format(addr_int)
        type_flags = {
            'program': ' /Program',
            'read': ' /Read',
            'write': ' /Write',
            'readwrite': ' /ReadWrite',
        }
        cmd_str += type_flags.get(bp_type, '')
        if size is not None:
            cmd_str += " /Size {0}".format(size)
        return self.cmd(cmd_str)

    def delete_breakpoint(self, address=None):
        """Delete breakpoint(s).

        Args:
            address: Specific address to delete, or None to delete all.
        """
        if address is not None:
            addr_int, _ = _parse_address(address)
            return self.cmd("Break.Delete 0x{0:X}".format(addr_int))
        return self.cmd("Break.Delete /ALL")

    def list_breakpoints(self):
        """List all breakpoints.

        Returns:
            Breakpoint info as text string.
        """
        return self.cmd_with_result("PRINT Break.List()")

    # ------------------------------------------------------------------
    # Variables and symbols
    # ------------------------------------------------------------------

    def read_variable(self, name):
        """Read a C/C++ variable value.

        Args:
            name: Variable name (e.g. 'myVar', 'myStruct.field')

        Returns:
            Variable value as string.
        """
        return self.eval_expression("Var.VALUE({0})".format(name))

    def write_variable(self, name, value):
        """Write a C/C++ variable.

        Args:
            name: Variable name
            value: Value to write (as string expression)
        """
        return self.cmd("Var.Set {0}={1}".format(name, value))

    def get_symbol_address(self, name):
        """Get the address of a symbol (function or variable).

        Args:
            name: Symbol name

        Returns:
            Address as string (may include access class prefix).
        """
        return self.eval_expression("sYmbol.BEGIN({0})".format(name))

    # ------------------------------------------------------------------
    # Program loading and scripts
    # ------------------------------------------------------------------

    def load_elf(self, path):
        """Load an ELF binary to target.

        Args:
            path: Path to ELF file (on the TRACE32 host filesystem)
        """
        return self.cmd("Data.LOAD.Elf {0}".format(path))

    def load_binary(self, path, address):
        """Load a raw binary file to target memory.

        Args:
            path: Path to binary file
            address: Load address
        """
        addr_int, access = _parse_address(address)
        prefix = "{0}:".format(access) if access else ""
        return self.cmd("Data.LOAD.Binary {0} {1}0x{2:X}".format(path, prefix, addr_int))

    def run_script(self, path):
        """Execute a PRACTICE (.cmm) script.

        Args:
            path: Path to .cmm file (on the TRACE32 host filesystem)
        """
        return self.cmd("DO {0}".format(path))

    def get_practice_state(self):
        """Check if a PRACTICE script is currently running.

        Returns:
            Dict with 'running' (bool).
        """
        result = self.eval_expression("PRACTICE.ISRUNNING()")
        running = result.strip().upper() in ('TRUE', '1', 'TRUE()')
        return {'running': running}

    # ------------------------------------------------------------------
    # Utility / info
    # ------------------------------------------------------------------

    def get_version(self):
        """Get TRACE32 software version string."""
        return self.eval_expression("VERSION.SOFTWARE()")

    def get_cpu(self):
        """Get currently configured CPU name."""
        return self.eval_expression("SYStem.CPU()")

    def window_cmd(self, command):
        """Execute a command in the TRACE32 GUI context.

        Useful for opening windows like Data.dump, Var.Watch, etc.
        """
        return self.cmd(command)

    # ------------------------------------------------------------------
    # Internal protocol methods
    # ------------------------------------------------------------------

    def _ensure_connected(self):
        """Raise error if not connected."""
        if not self._connected or self._sock is None:
            raise Trace32Error("Not connected to TRACE32. Call connect() first.")

    def _next_msg_id(self):
        """Get next message sequence ID (0-255 wrapping)."""
        mid = self._msg_id
        self._msg_id = (self._msg_id + 1) & 0xFF
        return mid

    def _build_msg(self, cmd, subcmd, payload=None):
        """Build a protocol message.

        Message format: [cmd:1] [subcmd:1] [msgid:1] [payload:N]

        Args:
            cmd: Command byte
            subcmd: Sub-command byte
            payload: Optional payload as bytearray

        Returns:
            bytearray with complete message.
        """
        msg = bytearray()
        msg.append(cmd & 0xFF)
        msg.append(subcmd & 0xFF)
        msg.append(self._next_msg_id())
        if payload:
            msg.extend(payload)
        return msg

    def _send(self, data):
        """Send a framed message over TCP.

        TCP frame: [length:4 bytes LE] [message data]

        Args:
            data: bytearray with message data.
        """
        frame = struct.pack('<I', len(data)) + bytes(data)
        self._sock.sendall(frame)

    def _recv(self):
        """Receive a framed message over TCP.

        Returns:
            bytearray with message data (without length header).
        """
        header = self._recv_exact(4)
        length = struct.unpack('<I', header)[0]
        if length > DEFAULT_PACKLEN_TCP:
            raise Trace32Error("Response too large: {0} bytes".format(length))
        if length == 0:
            return bytearray()
        data = self._recv_exact(length)
        return bytearray(data)

    def _recv_exact(self, n):
        """Receive exactly n bytes from socket.

        Args:
            n: Number of bytes to receive.

        Returns:
            bytes object of length n.
        """
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except socket.timeout:
                raise Trace32Error("Receive timeout")
            if not chunk:
                raise Trace32Error("Connection closed by TRACE32")
            buf.extend(chunk)
        return bytes(buf)

    def _check_response(self, resp, expected_cmd=None):
        """Validate a protocol response.

        Args:
            resp: bytearray response data
            expected_cmd: Expected command echo byte (optional)

        Returns:
            The response bytearray.

        Raises:
            Trace32Error: If response indicates an error.
        """
        if len(resp) < 3:
            raise Trace32Error("Response too short ({0} bytes)".format(len(resp)))
        if expected_cmd is not None and resp[0] != expected_cmd:
            raise Trace32Error(
                "Unexpected response cmd: expected 0x{0:02X}, got 0x{1:02X}".format(
                    expected_cmd, resp[0]))
        status = resp[2]
        if status != ERR_OK:
            raise Trace32Error("Command failed", error_code=status)
        return resp

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def __repr__(self):
        if self._connected:
            return "Trace32Client(connected={0}:{1})".format(self._host, self._port)
        return "Trace32Client(disconnected)"
