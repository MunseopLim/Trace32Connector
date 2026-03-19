#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for t32.client module.

Uses a mock TCP server to simulate TRACE32 PowerView responses,
so tests can run without actual TRACE32 hardware.
"""
from __future__ import print_function

import unittest
import socket
import struct
import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from t32.client import Trace32Client, Trace32Error, _to_bytes, _parse_address
from t32.constants import (
    CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING,
    CMD_DEVICE_SPECIFIC, CMD_GETMSG,
    SUBCMD_GET_STATE, SUBCMD_READ_MEMORY, SUBCMD_WRITE_MEMORY,
    SUBCMD_READ_REG_BY_NAME, SUBCMD_READ_PP,
    DEV_ICD, ERR_OK,
    STATE_STOPPED, STATE_RUNNING,
)


# ======================================================================
# Mock TRACE32 TCP Server
# ======================================================================

class MockTrace32Server(object):
    """Simulates a TRACE32 PowerView RCL/NETTCP server for testing.

    Responds to protocol messages with valid responses.
    """

    def __init__(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(('127.0.0.1', 0))
        self._server_sock.listen(1)
        self.port = self._server_sock.getsockname()[1]
        self._running = False
        self._thread = None
        self._client_sock = None
        self._target_state = STATE_STOPPED
        self._memory = {}        # addr -> bytes
        self._registers = {}     # name -> (lo, hi)
        self._pp = 0x08001000    # program counter
        self._last_message = ''  # AREA window message
        self._request_log = []   # log of received requests

    def start(self):
        """Start the mock server in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._serve)
        self._thread.daemon = True
        self._thread.start()
        time.sleep(0.05)  # let server start

    def stop(self):
        """Stop the mock server."""
        self._running = False
        if self._client_sock:
            try:
                self._client_sock.close()
            except Exception:
                pass
        try:
            self._server_sock.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def set_memory(self, addr, data):
        """Pre-populate memory for read tests."""
        if isinstance(data, str):
            import binascii
            data = binascii.unhexlify(data)
        for i, b in enumerate(bytearray(data)):
            self._memory[addr + i] = b

    def set_register(self, name, value):
        """Pre-populate a register value."""
        self._registers[name.upper()] = (value & 0xFFFFFFFF, (value >> 32) & 0xFFFFFFFF)

    def set_pp(self, value):
        """Set the program counter."""
        self._pp = value

    def set_state(self, state):
        """Set the target state."""
        self._target_state = state

    def _serve(self):
        """Accept one connection and handle requests."""
        self._server_sock.settimeout(2.0)
        try:
            self._client_sock, _ = self._server_sock.accept()
            self._client_sock.settimeout(1.0)
        except socket.timeout:
            return

        while self._running:
            try:
                msg = self._recv_msg()
                if msg is None:
                    break
                resp = self._handle_msg(msg)
                if resp is not None:
                    self._send_msg(resp)
            except socket.timeout:
                continue
            except Exception:
                break

    def _recv_msg(self):
        """Receive one framed message."""
        try:
            header = self._recv_exact(4)
        except Exception:
            return None
        if header is None:
            return None
        length = struct.unpack('<I', header)[0]
        if length == 0:
            return bytearray()
        data = self._recv_exact(length)
        return bytearray(data) if data else None

    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self._client_sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _send_msg(self, data):
        """Send one framed message."""
        frame = struct.pack('<I', len(data)) + bytes(data)
        self._client_sock.sendall(frame)

    def _handle_msg(self, msg):
        """Route a message and build response."""
        if len(msg) < 3:
            return None

        cmd = msg[0]
        subcmd = msg[1]
        msgid = msg[2]
        self._request_log.append((cmd, subcmd, msgid, bytes(msg[3:])))

        if cmd == CMD_ATTACH:
            # ATTACH response: [cmd, dev_type, ERR_OK]
            return bytearray([CMD_ATTACH, subcmd, ERR_OK])

        elif cmd == CMD_PING:
            return bytearray([CMD_PING, 0x00, ERR_OK])

        elif cmd == CMD_EXECUTE_PRACTICE:
            # Extract command string
            payload = msg[3:]
            cmd_str = bytes(payload).split(b'\x00')[0].decode('ascii', errors='replace')
            # Simulate PRINT command -> store in message area
            if cmd_str.upper().startswith('PRINT '):
                expr = cmd_str[6:].strip()
                self._last_message = self._eval_expr(expr)
            return bytearray([CMD_EXECUTE_PRACTICE, 0x00, ERR_OK])

        elif cmd == CMD_GETMSG:
            resp = bytearray([CMD_GETMSG, 0x00, ERR_OK])
            resp.extend(struct.pack('<H', 0))  # mode
            resp.extend(_to_bytes(self._last_message))
            resp.append(0x00)  # null terminator
            return resp

        elif cmd == CMD_DEVICE_SPECIFIC:
            return self._handle_device(subcmd, msg[3:])

        return bytearray([cmd, subcmd, ERR_OK])

    def _handle_device(self, subcmd, payload):
        """Handle device-specific sub-commands."""
        if subcmd == SUBCMD_GET_STATE:
            resp = bytearray([CMD_DEVICE_SPECIFIC, subcmd, ERR_OK])
            resp.append(self._target_state)
            return resp

        elif subcmd == SUBCMD_READ_PP:
            resp = bytearray([CMD_DEVICE_SPECIFIC, subcmd, ERR_OK])
            resp.extend(struct.pack('<I', self._pp))
            return resp

        elif subcmd == SUBCMD_READ_REG_BY_NAME:
            name = bytes(payload).split(b'\x00')[0].decode('ascii').upper()
            lo, hi = self._registers.get(name, (0, 0))
            resp = bytearray([CMD_DEVICE_SPECIFIC, subcmd, ERR_OK])
            resp.extend(struct.pack('<I', lo))
            resp.extend(struct.pack('<I', hi))
            return resp

        elif subcmd == SUBCMD_READ_MEMORY:
            addr = struct.unpack_from('<I', bytes(payload), 0)[0]
            size = struct.unpack_from('<H', bytes(payload), 4)[0]
            # access_class = payload[6]
            data = bytearray(size)
            for i in range(size):
                data[i] = self._memory.get(addr + i, 0xFF)
            resp = bytearray([CMD_DEVICE_SPECIFIC, subcmd, ERR_OK])
            resp.extend(data)
            return resp

        elif subcmd == SUBCMD_WRITE_MEMORY:
            addr = struct.unpack_from('<I', bytes(payload), 0)[0]
            size = struct.unpack_from('<H', bytes(payload), 4)[0]
            # access_class = payload[6]
            data = payload[7:7 + size]
            for i in range(size):
                if i < len(data):
                    self._memory[addr + i] = data[i]
            resp = bytearray([CMD_DEVICE_SPECIFIC, subcmd, ERR_OK])
            return resp

        # Default OK
        return bytearray([CMD_DEVICE_SPECIFIC, subcmd, ERR_OK])

    def _eval_expr(self, expr):
        """Simulate expression evaluation for PRINT commands."""
        e = expr.upper()
        if e.startswith('REGISTER(') or e.startswith('REGISTER.'):
            name = expr.split('(')[1].rstrip(')')
            lo, hi = self._registers.get(name.upper(), (0, 0))
            val = (hi << 32) | lo
            return "0x{0:X}".format(val)
        elif e.startswith('VAR.VALUE('):
            return "42"
        elif e.startswith('SYMBOL.BEGIN(') or e.startswith('SYMBOL.BEGIN('):
            return "0x08001000"
        elif e == 'VERSION.SOFTWARE()':
            return "TRACE32 Mock Server"
        elif e == 'PRACTICE.ISRUNNING()':
            return "FALSE"
        return expr


# ======================================================================
# Test: Utility Functions
# ======================================================================

class TestToBytes(unittest.TestCase):
    def test_bytes_passthrough(self):
        self.assertEqual(_to_bytes(b'hello'), b'hello')

    def test_str_to_bytes(self):
        result = _to_bytes('hello')
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, b'hello')

    def test_empty_string(self):
        self.assertEqual(_to_bytes(''), b'')
        self.assertEqual(_to_bytes(b''), b'')


class TestParseAddress(unittest.TestCase):
    def test_integer(self):
        addr, access = _parse_address(0x1000)
        self.assertEqual(addr, 0x1000)
        self.assertIsNone(access)

    def test_hex_string(self):
        addr, access = _parse_address("0x2000")
        self.assertEqual(addr, 0x2000)
        self.assertIsNone(access)

    def test_decimal_string(self):
        addr, access = _parse_address("4096")
        self.assertEqual(addr, 4096)
        self.assertIsNone(access)

    def test_access_prefix_data(self):
        addr, access = _parse_address("D:0x1000")
        self.assertEqual(addr, 0x1000)
        self.assertEqual(access, 'D')

    def test_access_prefix_program(self):
        addr, access = _parse_address("P:0x2000")
        self.assertEqual(addr, 0x2000)
        self.assertEqual(access, 'P')

    def test_access_prefix_sd(self):
        addr, access = _parse_address("SD:0x3000")
        self.assertEqual(addr, 0x3000)
        self.assertEqual(access, 'SD')


# ======================================================================
# Test: Trace32Error
# ======================================================================

class TestTrace32Error(unittest.TestCase):
    def test_basic_error(self):
        err = Trace32Error("test error")
        self.assertIn("test error", str(err))

    def test_error_with_code(self):
        err = Trace32Error("failed", error_code=0x04)
        self.assertIn("0x04", str(err))

    def test_error_inherits_exception(self):
        err = Trace32Error("test")
        self.assertIsInstance(err, Exception)


# ======================================================================
# Test: Client without connection
# ======================================================================

class TestClientDisconnected(unittest.TestCase):
    def test_initial_state(self):
        client = Trace32Client()
        self.assertFalse(client.connected)

    def test_cmd_raises_when_disconnected(self):
        client = Trace32Client()
        with self.assertRaises(Trace32Error):
            client.cmd("SYStem.Up")

    def test_get_state_raises_when_disconnected(self):
        client = Trace32Client()
        with self.assertRaises(Trace32Error):
            client.get_state()

    def test_ping_raises_when_disconnected(self):
        client = Trace32Client()
        with self.assertRaises(Trace32Error):
            client.ping()

    def test_disconnect_when_not_connected(self):
        client = Trace32Client()
        # Should not raise
        client.disconnect()

    def test_repr_disconnected(self):
        client = Trace32Client()
        self.assertIn('disconnected', repr(client))

    def test_connect_to_invalid_host(self):
        client = Trace32Client()
        with self.assertRaises(Trace32Error):
            client.connect(host='192.0.2.1', port=1, timeout=0.5)


# ======================================================================
# Test: Client with Mock Server
# ======================================================================

class TestClientWithMockServer(unittest.TestCase):
    """Integration tests using the mock TRACE32 server."""

    def setUp(self):
        self.server = MockTrace32Server()
        self.server.start()
        self.client = Trace32Client()
        self.client.connect(host='127.0.0.1', port=self.server.port, timeout=5.0)

    def tearDown(self):
        self.client.disconnect()
        self.server.stop()

    def test_connect_success(self):
        self.assertTrue(self.client.connected)

    def test_repr_connected(self):
        self.assertIn('127.0.0.1', repr(self.client))

    def test_ping(self):
        result = self.client.ping()
        self.assertTrue(result)

    def test_cmd_success(self):
        result = self.client.cmd("SYStem.Up")
        self.assertTrue(result)

    def test_cmd_sends_correct_bytes(self):
        self.client.cmd("Break")
        # Verify the server received the command
        found = False
        for cmd, subcmd, msgid, payload in self.server._request_log:
            if cmd == CMD_EXECUTE_PRACTICE:
                cmd_str = bytes(payload).split(b'\x00')[0]
                if cmd_str == b'Break':
                    found = True
                    break
        self.assertTrue(found, "Server did not receive 'Break' command")

    def test_get_state_stopped(self):
        self.server.set_state(STATE_STOPPED)
        state = self.client.get_state()
        self.assertEqual(state['state_code'], STATE_STOPPED)
        self.assertEqual(state['state_name'], 'stopped')

    def test_get_state_running(self):
        self.server.set_state(STATE_RUNNING)
        state = self.client.get_state()
        self.assertEqual(state['state_code'], STATE_RUNNING)
        self.assertEqual(state['state_name'], 'running')

    def test_read_memory(self):
        self.server.set_memory(0x1000, b'\xDE\xAD\xBE\xEF')
        data = self.client.read_memory(0x1000, 4)
        self.assertEqual(data, b'\xDE\xAD\xBE\xEF')

    def test_read_memory_hex(self):
        self.server.set_memory(0x2000, b'\x01\x02\x03\x04')
        hex_str = self.client.read_memory_hex(0x2000, 4)
        self.assertEqual(hex_str, '01020304')

    def test_read_memory_with_access_prefix(self):
        self.server.set_memory(0x3000, b'\xAA\xBB')
        data = self.client.read_memory("D:0x3000", 2)
        self.assertEqual(data, b'\xAA\xBB')

    def test_read_memory_uninitialized(self):
        """Uninitialized memory returns 0xFF in mock server."""
        data = self.client.read_memory(0x9000, 4)
        self.assertEqual(data, b'\xFF\xFF\xFF\xFF')

    def test_write_memory(self):
        self.client.write_memory(0x4000, b'\x11\x22\x33\x44')
        # Verify by reading back
        data = self.client.read_memory(0x4000, 4)
        self.assertEqual(data, b'\x11\x22\x33\x44')

    def test_write_memory_hex_string(self):
        self.client.write_memory(0x5000, 'AABBCCDD')
        data = self.client.read_memory(0x5000, 4)
        self.assertEqual(data, b'\xAA\xBB\xCC\xDD')

    def test_read_register(self):
        self.server.set_register('PC', 0x08001234)
        value = self.client.read_register('PC')
        self.assertEqual(value, 0x08001234)

    def test_read_register_64bit(self):
        self.server.set_register('X0', 0x0000000100000002)
        value = self.client.read_register('X0')
        self.assertEqual(value, 0x0000000100000002)

    def test_read_register_case_insensitive(self):
        self.server.set_register('SP', 0x20010000)
        value = self.client.read_register('sp')
        # Mock server uppercases the name, so 'sp' -> 'SP' should work
        self.assertEqual(value, 0x20010000)

    def test_read_pc(self):
        self.server.set_pp(0x08002000)
        pc = self.client.read_pc()
        self.assertEqual(pc, 0x08002000)

    def test_eval_expression(self):
        result = self.client.eval_expression("VERSION.SOFTWARE()")
        self.assertEqual(result, "TRACE32 Mock Server")

    def test_get_message(self):
        # First send a PRINT command to populate message
        self.client.cmd("PRINT VERSION.SOFTWARE()")
        msg = self.client.get_message()
        self.assertEqual(msg['text'], "TRACE32 Mock Server")
        self.assertIn('mode', msg)

    def test_go(self):
        result = self.client.go()
        self.assertTrue(result)

    def test_break_target(self):
        result = self.client.break_target()
        self.assertTrue(result)

    def test_step(self):
        result = self.client.step()
        self.assertTrue(result)

    def test_step_multiple(self):
        result = self.client.step(5)
        self.assertTrue(result)

    def test_step_over(self):
        result = self.client.step_over()
        self.assertTrue(result)

    def test_set_breakpoint(self):
        result = self.client.set_breakpoint(0x08001000)
        self.assertTrue(result)

    def test_set_breakpoint_read(self):
        result = self.client.set_breakpoint(0x20000000, bp_type='read')
        self.assertTrue(result)

    def test_delete_breakpoint(self):
        result = self.client.delete_breakpoint(0x08001000)
        self.assertTrue(result)

    def test_delete_all_breakpoints(self):
        result = self.client.delete_breakpoint()
        self.assertTrue(result)

    def test_read_variable(self):
        result = self.client.read_variable("myVar")
        self.assertEqual(result, "42")

    def test_write_register_via_cmd(self):
        result = self.client.write_register("R0", 0x1234)
        self.assertTrue(result)

    def test_write_variable(self):
        result = self.client.write_variable("myVar", "100")
        self.assertTrue(result)

    def test_run_script(self):
        result = self.client.run_script("test.cmm")
        self.assertTrue(result)

    def test_load_elf(self):
        result = self.client.load_elf("firmware.elf")
        self.assertTrue(result)

    def test_context_manager(self):
        """Test with-statement support."""
        server = MockTrace32Server()
        server.start()
        try:
            with Trace32Client() as client:
                client.connect(host='127.0.0.1', port=server.port, timeout=5.0)
                self.assertTrue(client.connected)
                client.ping()
            # After with-block, should be disconnected
            self.assertFalse(client.connected)
        finally:
            server.stop()

    def test_reconnect(self):
        """Disconnect and reconnect."""
        self.client.disconnect()
        self.assertFalse(self.client.connected)
        # Need a new server since old connection is dead
        server2 = MockTrace32Server()
        server2.start()
        try:
            self.client.connect(host='127.0.0.1', port=server2.port, timeout=5.0)
            self.assertTrue(self.client.connected)
            self.client.ping()
        finally:
            self.client.disconnect()
            server2.stop()


# ======================================================================
# Test: Protocol Message Building
# ======================================================================

class TestProtocolMessages(unittest.TestCase):
    """Test the internal message building of the client."""

    def test_build_msg_basic(self):
        client = Trace32Client()
        msg = client._build_msg(0x73, 0x00)
        self.assertEqual(len(msg), 3)
        self.assertEqual(msg[0], 0x73)
        self.assertEqual(msg[1], 0x00)

    def test_build_msg_with_payload(self):
        client = Trace32Client()
        payload = bytearray(b'\x01\x02\x03')
        msg = client._build_msg(0x72, 0x00, payload)
        self.assertEqual(len(msg), 6)
        self.assertEqual(msg[0], 0x72)
        self.assertEqual(msg[3], 0x01)
        self.assertEqual(msg[4], 0x02)
        self.assertEqual(msg[5], 0x03)

    def test_msg_id_increments(self):
        client = Trace32Client()
        msg1 = client._build_msg(0x73, 0x00)
        msg2 = client._build_msg(0x73, 0x00)
        self.assertEqual(msg2[2], msg1[2] + 1)

    def test_msg_id_wraps_at_256(self):
        client = Trace32Client()
        client._msg_id = 255
        msg = client._build_msg(0x73, 0x00)
        self.assertEqual(msg[2], 255)
        msg2 = client._build_msg(0x73, 0x00)
        self.assertEqual(msg2[2], 0)


# ======================================================================
# Test: Memory read/write round-trip
# ======================================================================

class TestMemoryRoundTrip(unittest.TestCase):
    """Test various memory read/write patterns."""

    def setUp(self):
        self.server = MockTrace32Server()
        self.server.start()
        self.client = Trace32Client()
        self.client.connect(host='127.0.0.1', port=self.server.port, timeout=5.0)

    def tearDown(self):
        self.client.disconnect()
        self.server.stop()

    def test_single_byte(self):
        self.client.write_memory(0x100, b'\x42')
        data = self.client.read_memory(0x100, 1)
        self.assertEqual(data, b'\x42')

    def test_large_block(self):
        """Write and read 1024 bytes."""
        block = bytes(bytearray(range(256)) * 4)
        self.client.write_memory(0x10000, block)
        data = self.client.read_memory(0x10000, 1024)
        self.assertEqual(data, block)

    def test_aligned_word_access(self):
        self.client.write_memory(0x200, b'\x78\x56\x34\x12')
        data = self.client.read_memory(0x200, 4)
        value = struct.unpack('<I', data)[0]
        self.assertEqual(value, 0x12345678)

    def test_hex_string_write(self):
        self.client.write_memory(0x300, 'CAFEBABE')
        data = self.client.read_memory(0x300, 4)
        self.assertEqual(data, b'\xCA\xFE\xBA\xBE')


if __name__ == '__main__':
    unittest.main()
