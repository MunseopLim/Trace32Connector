#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 Remote API Client (NETASSIST/UDP).

Pure Python UDP client for communicating with TRACE32 PowerView
via the Remote Control (RCL/NETASSIST) protocol.

Compatible with Python 2.7 and 3.4+.
No external dependencies.

TRACE32 PowerView setup (config.t32):
    RCL=NETASSIST
    PORT=20000
    PACKLEN=1024

Usage:
    from t32.client import Trace32Client
    client = Trace32Client()
    client.connect('localhost', 20000)
    client.cmd('SYStem.Up')
    state = client.get_state()
    client.disconnect()

Protocol reference: <T32_DIR>/demo/api/capi/src/hremote.c, hlinknet.c
"""
from __future__ import print_function

import select
import socket
import struct
import sys
import threading
import binascii

from .constants import (
    CMD_NOP, CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING,
    CMD_DEVICE_SPECIFIC, CMD_GETMSG, CMD_TERMINATE,
    SUBCMD_GET_STATE, SUBCMD_READ_PP, SUBCMD_READ_REG_BY_NAME,
    SUBCMD_WRITE_REG_BY_NAME, SUBCMD_READ_MEMORY, SUBCMD_WRITE_MEMORY,
    SUBCMD_EXECUTE_PRACTICE,
    DEV_ICD,
    STATE_NAMES,
    ACCESS_CLASSES, ACCESS_DATA,
    ERR_OK, ERROR_NAMES,
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT, DEFAULT_PACKLEN,
    T32_API_CONNECT, T32_API_CONNECT_OK,
    T32_API_CONNECT_REFUSED_IP, T32_API_CONNECT_ERROR,
    T32_API_SYNCREQUEST, T32_API_SYNCACKN, T32_API_SYNCBACK,
    T32_API_TRANSMIT, T32_API_RECEIVE, T32_API_NOTIFICATION,
    T32_API_HANDSHAKE,
    T32_MSG_LHANDLE, T32_MSG_LRETRY,
    MAGIC_PATTERN, MAXRETRY,
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
    """TRACE32 Remote API client over UDP (NETASSIST protocol).

    Implements the binary protocol for direct communication with
    TRACE32 PowerView via UDP sockets. All high-level debugging
    operations are available as methods.

    Protocol based on hremote.c / hlinknet.c reference implementation.
    """

    def __init__(self):
        self._sock = None
        self._msg_id = 0
        self._current_msg_id = 0
        self._connected = False
        self._host = None
        self._port = None
        self._target_addr = None
        self._local_port = 0
        self._packet_size = DEFAULT_PACKLEN
        self._transmit_seq = 0
        self._receive_seq = 0
        self._last_receive_seq = 0
        self._last_transmit_seq = 0
        self._last_transmit_data = None
        self._last_transmit_size = 0
        self._receive_toggle_bit = -1
        self._poll_timeout = DEFAULT_TIMEOUT
        self._lock = threading.Lock()

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

        Performs UDP handshake, sequence sync, and device attach.

        Args:
            host: Hostname or IP address (default: localhost)
            port: RCL port number (default: 20000)
            timeout: Socket timeout in seconds (default: 10.0)
            device: Device type (default: DEV_ICD)

        Raises:
            Trace32Error: If connection, sync, or attach fails.
        """
        if self._connected:
            self.disconnect()

        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(('', 0))
            self._local_port = self._sock.getsockname()[1]
        except socket.error as e:
            self._sock = None
            raise Trace32Error("Cannot create UDP socket: {0}".format(e))

        self._host = host
        self._port = port
        self._target_addr = (host, port)
        self._poll_timeout = timeout
        self._packet_size = DEFAULT_PACKLEN
        self._transmit_seq = 1
        self._msg_id = 0
        self._receive_toggle_bit = -1
        self._last_transmit_data = None
        self._last_transmit_size = 0

        # Set socket buffer sizes
        try:
            self._sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_RCVBUF, 18000)
            self._sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, 18000)
        except socket.error:
            pass

        # Connection handshake
        connected = False
        for _ in range(10):
            result = self._connection()
            if result == 1:
                connected = True
                break
            elif result == 2:
                self._cleanup_socket()
                raise Trace32Error(
                    "Connection refused by TRACE32 at {0}:{1}".format(
                        host, port))

        if not connected:
            self._cleanup_socket()
            raise Trace32Error(
                "TRACE32 not responding at {0}:{1}. "
                "Ensure PowerView is running with RCL=NETASSIST and "
                "PORT={1} in config.t32".format(host, port))

        # Sync
        try:
            self._sync()
        except Trace32Error:
            self._cleanup_socket()
            raise

        # Attach
        try:
            msg = self._build_msg(CMD_ATTACH, device)
            resp = self._exchange(msg)
            if resp[1] != ERR_OK:
                raise Trace32Error("ATTACH failed", error_code=resp[1])
        except Trace32Error:
            self._cleanup_socket()
            raise
        except Exception as e:
            self._cleanup_socket()
            raise Trace32Error("ATTACH failed: {0}".format(e))

        self._connected = True
        self._host = host
        self._port = port

    def disconnect(self):
        """Disconnect from TRACE32."""
        with self._lock:
            if self._sock:
                try:
                    msg = self._build_msg(CMD_NOP, 0)
                    self._transmit(msg)
                except Exception:
                    pass
                self._cleanup_socket()
            self._connected = False

    def _exchange(self, msg):
        """Thread-safe transmit and receive. Used by all protocol methods."""
        with self._lock:
            self._transmit(msg)
            return self._receive()

    def ping(self):
        """Ping TRACE32 to check connection is alive.

        Returns:
            True if TRACE32 responds.

        Raises:
            Trace32Error: If ping fails.
        """
        self._ensure_connected()
        msg = self._build_msg(CMD_PING, 0)
        resp = self._exchange(msg)
        self._check_response(resp)
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
        msg = self._build_msg(
            CMD_EXECUTE_PRACTICE, SUBCMD_EXECUTE_PRACTICE, payload)
        resp = self._exchange(msg)
        status = resp[1]
        if status != ERR_OK:
            # Try to get error details from T32's message area
            detail = ''
            try:
                detail = self.get_message()['text']
            except Exception:
                pass
            err_msg = "Command '{0}' failed".format(command)
            if detail:
                err_msg = "{0}: {1}".format(err_msg, detail)
            raise Trace32Error(err_msg, error_code=status)
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
        resp = self._exchange(msg)
        self._check_response(resp)

        mode = 0
        text = ''
        if len(resp) > 7:
            # Mode is a 4-byte dword at resp[3:7], truncated to 16-bit
            mode = struct.unpack_from('<I', bytes(resp[3:7]))[0] & 0xFFFF
            text_data = resp[7:]
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
        resp = self._exchange(msg)
        self._check_response(resp)

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
            size: Number of bytes to read
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

        # Payload format: [addr:4LE][access:1][0:1][size:2LE]
        payload = bytearray()
        payload.extend(struct.pack('<I', addr_int))
        payload.append(access_code)
        payload.append(0)
        payload.extend(struct.pack('<H', size))

        msg = self._build_msg(CMD_DEVICE_SPECIFIC, SUBCMD_READ_MEMORY, payload)
        resp = self._exchange(msg)
        self._check_response(resp)

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

        # Payload format: [addr:4LE][access:1][0:1][size:2LE][data:N]
        payload = bytearray()
        payload.extend(struct.pack('<I', addr_int))
        payload.append(access_code)
        payload.append(0)
        payload.extend(struct.pack('<H', len(data)))
        payload.extend(data)

        # LEN byte covers fixed header only (10), not variable data
        msg = self._build_msg(
            CMD_DEVICE_SPECIFIC, SUBCMD_WRITE_MEMORY, payload, msg_len=10)
        resp = self._exchange(msg)
        self._check_response(resp)
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
        resp = self._exchange(msg)
        self._check_response(resp)

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
        resp = self._exchange(msg)
        self._check_response(resp)

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
    # Internal: connection setup
    # ------------------------------------------------------------------

    def _cleanup_socket(self):
        """Close and reset the UDP socket."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _connection(self):
        """Perform UDP connection handshake with TRACE32.

        Returns:
            1 on success, 2 on refused, 0 on no response.
        """
        packet = bytearray(self._packet_size)
        packet[0] = T32_API_CONNECT
        packet[1] = 0
        struct.pack_into('<H', packet, 2, self._transmit_seq & 0xFFFF)
        struct.pack_into('<H', packet, 4, self._port & 0xFFFF)
        struct.pack_into('<H', packet, 6, self._local_port & 0xFFFF)
        packet[8:8 + len(MAGIC_PATTERN)] = bytearray(MAGIC_PATTERN)

        self._sock.sendto(bytes(packet), self._target_addr)

        resp = self._udp_recv(self._poll_timeout)
        if resp is None:
            return 0

        if len(resp) < 16:
            return 0

        if bytes(resp[8:16]) != MAGIC_PATTERN:
            return 0

        if resp[0] == T32_API_CONNECT_OK:
            self._receive_seq = struct.unpack_from(
                '<H', bytes(resp[2:4]))[0]
            self._packet_size = len(resp)
            return 1

        if resp[0] in (T32_API_CONNECT_REFUSED_IP, T32_API_CONNECT_ERROR):
            return 2

        return 0

    def _sync(self):
        """Perform 3-way sync: SYNCREQUEST -> SYNCACKN -> SYNCBACK.

        Raises:
            Trace32Error: If sync fails after max attempts.
        """
        j = 0

        while True:
            # Send SYNCREQUEST
            packet = bytearray(16)
            packet[0] = T32_API_SYNCREQUEST
            packet[1] = 0
            struct.pack_into('<H', packet, 2, self._transmit_seq & 0xFFFF)
            struct.pack_into('<H', packet, 4, 0)
            struct.pack_into('<H', packet, 6, 0)
            packet[8:16] = bytearray(MAGIC_PATTERN)

            self._sock.sendto(bytes(packet), self._target_addr)

            # Wait for SYNCACKN
            while True:
                j += 1
                if j > 20:
                    raise Trace32Error("Sync failed: no response from TRACE32")

                resp = self._udp_recv(self._poll_timeout)
                if resp is None:
                    raise Trace32Error("Sync failed: timeout")

                if len(resp) != 16:
                    continue

                # Type 0x05: T32 requests re-sync
                if resp[0] == 0x05:
                    break  # resend SYNCREQUEST

                if resp[0] != T32_API_SYNCACKN:
                    continue

                if bytes(resp[8:16]) != MAGIC_PATTERN:
                    continue

                # Got SYNCACKN - extract server's sequence
                self._receive_seq = struct.unpack_from(
                    '<H', bytes(resp[2:4]))[0]
                self._last_receive_seq = (
                    self._receive_seq - 100) & 0xFFFF

                # Send SYNCBACK
                packet[0] = T32_API_SYNCBACK
                self._sock.sendto(bytes(packet), self._target_addr)
                return

    # ------------------------------------------------------------------
    # Internal: message building
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

    def _build_msg(self, cmd, subcmd, payload=None, msg_len=None):
        """Build a protocol message.

        Message format: [LEN:1][CMD:1][SUBCMD:1][MSGID:1][payload:N]
        LEN = number of bytes from SUBCMD to end (2 + payload_size),
        or 0 for extended format with 16-bit length at [4:6].

        Args:
            cmd: Command byte
            subcmd: Sub-command byte
            payload: Optional payload as bytearray
            msg_len: Override for LEN byte (used when LEN doesn't
                     cover all payload, e.g. write_memory)

        Returns:
            bytearray with complete message.
        """
        msg = bytearray()
        payload_data = payload if payload else bytearray()
        data_len = msg_len if msg_len is not None else (2 + len(payload_data))

        msgid = self._next_msg_id()
        self._current_msg_id = msgid

        if data_len < 0xFF:
            msg.append(data_len & 0xFF)
            msg.append(cmd & 0xFF)
            msg.append(subcmd & 0xFF)
            msg.append(msgid)
            msg.extend(payload_data)
        else:
            # Extended format: LEN=0, actual length as 16-bit word at [4:6]
            msg.append(0)
            msg.append(cmd & 0xFF)
            msg.append(subcmd & 0xFF)
            msg.append(msgid)
            msg.extend(struct.pack('<H', data_len + 2))
            msg.extend(payload_data)

        return msg

    # ------------------------------------------------------------------
    # Internal: UDP transport (NETASSIST protocol)
    # ------------------------------------------------------------------

    def _transmit(self, msg=None):
        """Transmit a message via UDP (LINE_Transmit equivalent).

        Prepends 5-byte internal header, pads to even length,
        and sends via _line_transmit.

        Args:
            msg: Application message bytearray, or None for empty ack.
        """
        if msg is None or len(msg) == 0:
            # Empty transmit (acknowledgment)
            self._last_transmit_data = bytearray(0)
            self._last_transmit_size = 0
            self._last_transmit_seq = self._transmit_seq
            self._line_transmit(bytearray(0), 0)
            return

        # Pad message to even length
        if len(msg) % 2 != 0:
            msg = msg + bytearray(1)

        # Prepend 5-byte internal header (all zeros)
        data = bytearray(5) + msg
        total_size = len(data)

        self._last_transmit_data = bytes(data)
        self._last_transmit_size = total_size
        self._last_transmit_seq = self._transmit_seq

        self._line_transmit(data, total_size)

    def _line_transmit(self, data, size):
        """Fragment and send data as UDP packets (LINE_LineTransmit equivalent).

        Each UDP packet: [0x11][continuation][seq_lo][seq_hi][data_chunk]
        Max data per packet: packet_size - 4

        Args:
            data: bytearray of data to send.
            size: Number of bytes to send (0 for empty ack).
        """
        if size == 0:
            # Send empty 4-byte ack packet
            packet = bytearray(4)
            packet[0] = T32_API_TRANSMIT
            packet[1] = 0
            struct.pack_into('<H', packet, 2, self._transmit_seq & 0xFFFF)
            self._sock.sendto(bytes(packet), self._target_addr)
            self._transmit_seq += 1
            return

        offset = 0
        while size > 0:
            chunk_size = min(size, self._packet_size - 4)
            continuation = 1 if size > chunk_size else 0

            packet = bytearray(4 + chunk_size)
            packet[0] = T32_API_TRANSMIT
            packet[1] = continuation
            struct.pack_into('<H', packet, 2, self._transmit_seq & 0xFFFF)
            packet[4:4 + chunk_size] = data[offset:offset + chunk_size]

            self._sock.sendto(bytes(packet), self._target_addr)

            self._transmit_seq += 1
            offset += chunk_size
            size -= chunk_size

    def _receive(self):
        """Receive and validate response (LINE_Receive equivalent).

        Handles retry logic, toggle bits, and message ID validation.

        Returns:
            bytearray with application data:
            [0]=CMD echo, [1]=status, [2]=MSGID, [3+]=payload

        Raises:
            Trace32Error: On receive failure.
        """
        retry = 0
        while retry < MAXRETRY:
            raw = self._line_receive()

            if len(raw) < 5:
                raise Trace32Error(
                    "Response too short: {0} bytes".format(len(raw)))

            flags = raw[0]     # T32_INBUFFER[-1]
            status = raw[3]    # T32_INBUFFER[2]
            msgid = raw[4]     # T32_INBUFFER[3]

            # Busy indicator - reset retry counter
            if status == 0xFE:
                self._receive_toggle_bit = -1
                retry = 0
                retry += 1
                continue

            # Wrong message ID - skip
            if msgid != self._current_msg_id:
                retry += 1
                continue

            # Retransmit request
            if flags & T32_MSG_LRETRY:
                response_toggle = bool(flags & T32_MSG_LHANDLE)
                if self._receive_toggle_bit == response_toggle:
                    self._transmit()  # empty ack
                    retry += 1
                    continue

            # Valid response - update toggle bit
            self._receive_toggle_bit = bool(flags & T32_MSG_LHANDLE)

            # Return from CMD echo onwards (skip flags byte and header byte)
            return bytearray(raw[2:])

        raise Trace32Error(
            "Receive failed after {0} retries".format(MAXRETRY))

    def _line_receive(self):
        """Receive and reassemble multi-packet UDP message (LINE_LineReceive equivalent).

        Handles packet reassembly, sequence checking, retransmission,
        and flow control.

        Returns:
            bytearray with raw message data (including flags byte).

        Raises:
            Trace32Error: On receive failure.
        """
        while True:  # retry on '+' or notification
            result = bytearray()
            start_seq = self._receive_seq
            expected_seq = start_seq
            completed = False
            need_retry = False

            while not completed:
                pkt = self._udp_recv(self._poll_timeout)
                if pkt is None:
                    raise Trace32Error("Receive timeout")

                # Single '+' byte: reset signal
                if len(pkt) == 1 and pkt[0] == 0x2B:
                    need_retry = True
                    break

                if len(pkt) <= 4:
                    raise Trace32Error(
                        "Packet too short: {0} bytes".format(len(pkt)))

                pkt_type = pkt[0]

                # Notification packet: restart receive
                if pkt_type == T32_API_NOTIFICATION:
                    need_retry = True
                    break

                if pkt_type != T32_API_RECEIVE:
                    raise Trace32Error(
                        "Unexpected packet type: 0x{0:02X}".format(pkt_type))

                # Check sequence number
                seq = struct.unpack_from('<H', bytes(pkt[2:4]))[0]

                if seq != expected_seq:
                    # Server requesting retransmission of previous message
                    if (seq == self._last_receive_seq
                            and self._last_transmit_data is not None
                            and self._last_transmit_size > 0):
                        self._retransmit_last()
                    continue

                expected_seq += 1
                continuation = pkt[1]
                result.extend(pkt[4:])

                # Safety check for oversized response
                if len(result) > 16640:
                    raise Trace32Error("Response too large")

                # Flow control handshake for multi-packet messages
                if continuation == 2:
                    self._send_handshake()

                if continuation == 0:
                    completed = True

            if need_retry:
                continue

            self._last_receive_seq = start_seq
            self._receive_seq = expected_seq
            return result

    def _udp_recv(self, timeout):
        """Receive a UDP packet with timeout.

        Args:
            timeout: Timeout in seconds.

        Returns:
            bytearray with packet data, or None on timeout.
        """
        try:
            readable, _, _ = select.select(
                [self._sock], [], [], timeout)
        except (select.error, socket.error):
            return None

        if not readable:
            return None

        try:
            data, addr = self._sock.recvfrom(self._packet_size + 256)
            return bytearray(data)
        except socket.error:
            return None

    def _retransmit_last(self):
        """Retransmit the last sent message."""
        if self._last_transmit_data is not None:
            self._transmit_seq = self._last_transmit_seq
            self._line_transmit(
                bytearray(self._last_transmit_data),
                self._last_transmit_size)

    def _send_handshake(self):
        """Send flow control handshake packet for multi-packet messages."""
        handshake = bytearray(16)
        handshake[0] = T32_API_HANDSHAKE
        handshake[8:8 + len(MAGIC_PATTERN)] = bytearray(MAGIC_PATTERN)
        self._sock.sendto(bytes(handshake), self._target_addr)

    # ------------------------------------------------------------------
    # Internal: response validation
    # ------------------------------------------------------------------

    def _check_response(self, resp):
        """Validate a protocol response.

        Response format: [header][status][MSGID][payload...]
        T32 does not echo the CMD byte in responses (verified in hremote.c:
        all functions only check T32_INBUFFER[2] for status).

        Args:
            resp: bytearray response data

        Returns:
            The response bytearray.

        Raises:
            Trace32Error: If response indicates an error.
        """
        if len(resp) < 3:
            raise Trace32Error("Response too short ({0} bytes)".format(len(resp)))
        status = resp[1]
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
