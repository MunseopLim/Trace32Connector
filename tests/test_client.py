#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for t32.client module.

Uses a mock UDP server to simulate TRACE32 PowerView NETASSIST responses,
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
    CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING, CMD_NOP,
    CMD_DEVICE_SPECIFIC, CMD_GETMSG,
    SUBCMD_GET_STATE, SUBCMD_READ_MEMORY, SUBCMD_WRITE_MEMORY,
    SUBCMD_READ_REG_BY_NAME, SUBCMD_READ_PP,
    DEV_ICD, ERR_OK,
    STATE_STOPPED, STATE_RUNNING,
    T32_API_CONNECT, T32_API_CONNECT_OK,
    T32_API_SYNCREQUEST, T32_API_SYNCACKN, T32_API_SYNCBACK,
    T32_API_TRANSMIT, T32_API_RECEIVE,
    MAGIC_PATTERN, DEFAULT_PACKLEN,
)


# ======================================================================
# Mock TRACE32 UDP Server (NETASSIST protocol)
# ======================================================================

class MockTrace32Server(object):
    """Simulates a TRACE32 PowerView RCL/NETASSIST server for testing.

    Responds to UDP protocol messages with valid responses.
    Protocol: Connection handshake, 3-way sync, then data exchange.
    """

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(('127.0.0.1', 0))
        self.port = self._sock.getsockname()[1]
        self._running = False
        self._thread = None
        self._client_addr = None
        self._transmit_seq = 0
        self._packet_size = DEFAULT_PACKLEN
        self._target_state = STATE_STOPPED
        self._memory = {}        # addr -> byte
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
        time.sleep(0.05)

    def stop(self):
        """Stop the mock server."""
        self._running = False
        try:
            self._sock.close()
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
        self._registers[name.upper()] = (
            value & 0xFFFFFFFF, (value >> 32) & 0xFFFFFFFF)

    def set_pp(self, value):
        """Set the program counter."""
        self._pp = value

    def set_state(self, state):
        """Set the target state."""
        self._target_state = state

    def _serve(self):
        """Main server loop - receive and handle UDP packets."""
        self._sock.settimeout(0.5)
        while self._running:
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break

            pkt = bytearray(data)
            if len(pkt) < 1:
                continue

            pkt_type = pkt[0]

            if pkt_type == T32_API_CONNECT:
                self._handle_connect(pkt, addr)
            elif pkt_type == T32_API_SYNCREQUEST:
                self._handle_sync(pkt, addr)
            elif pkt_type == T32_API_SYNCBACK:
                pass  # sync completion acknowledged
            elif pkt_type == T32_API_TRANSMIT:
                self._handle_data_packet(pkt, addr)

    def _handle_connect(self, pkt, addr):
        """Handle connection handshake request."""
        self._client_addr = addr
        self._transmit_seq = 1
        # Response: same size as request, with CONNECT_OK
        resp = bytearray(len(pkt))
        resp[0] = T32_API_CONNECT_OK
        resp[1] = 0
        struct.pack_into('<H', resp, 2, self._transmit_seq)
        resp[8:16] = bytearray(MAGIC_PATTERN)
        self._sock.sendto(bytes(resp), addr)

    def _handle_sync(self, pkt, addr):
        """Handle 3-way sync request."""
        self._client_addr = addr
        # Respond with SYNCACKN containing our transmit seq
        resp = bytearray(16)
        resp[0] = T32_API_SYNCACKN
        resp[1] = 0
        struct.pack_into('<H', resp, 2, self._transmit_seq)
        resp[8:16] = bytearray(MAGIC_PATTERN)
        self._sock.sendto(bytes(resp), addr)

    def _handle_data_packet(self, pkt, addr):
        """Handle incoming data packet."""
        self._client_addr = addr

        if len(pkt) <= 4:
            return  # empty ack, ignore

        msg_data = pkt[4:]  # strip 4-byte UDP header

        # Need at least 5 internal header + 4 app bytes
        if len(msg_data) < 9:
            return

        # Skip 5-byte internal header
        app_data = msg_data[5:]

        # Parse: [LEN][CMD][SUBCMD][MSGID][payload...]
        msg_len_byte = app_data[0]
        cmd = app_data[1]
        subcmd = app_data[2]
        msgid = app_data[3]

        if msg_len_byte == 0 and len(app_data) > 5:
            # Extended format: skip 2-byte length at [4:6]
            payload = app_data[6:]
        else:
            payload = app_data[4:]

        self._request_log.append((cmd, subcmd, msgid, bytes(payload)))

        # Handle and respond
        resp_data = self._process_command(cmd, subcmd, msgid, payload)
        if resp_data is not None:
            self._send_response(resp_data, addr)

    def _process_command(self, cmd, subcmd, msgid, payload):
        """Process a command and return response data."""
        if cmd == CMD_NOP:
            return None

        if cmd == CMD_ATTACH:
            return self._build_response(CMD_ATTACH, ERR_OK, msgid)

        if cmd == CMD_PING:
            return self._build_response(CMD_PING, ERR_OK, msgid)

        if cmd == CMD_EXECUTE_PRACTICE:
            cmd_str = bytes(payload).split(b'\x00')[0].decode(
                'ascii', errors='replace')
            if cmd_str.upper().startswith('PRINT '):
                expr = cmd_str[6:].strip()
                self._last_message = self._eval_expr(expr)
            return self._build_response(CMD_EXECUTE_PRACTICE, ERR_OK, msgid)

        if cmd == CMD_GETMSG:
            resp_payload = bytearray()
            resp_payload.extend(struct.pack('<I', 0))  # mode (dword)
            resp_payload.extend(_to_bytes(self._last_message))
            resp_payload.append(0x00)
            return self._build_response(CMD_GETMSG, ERR_OK, msgid, resp_payload)

        if cmd == CMD_DEVICE_SPECIFIC:
            return self._handle_device(subcmd, msgid, payload)

        return self._build_response(cmd, ERR_OK, msgid)

    def _handle_device(self, subcmd, msgid, payload):
        """Handle device-specific sub-commands."""
        if subcmd == SUBCMD_GET_STATE:
            return self._build_response(
                CMD_DEVICE_SPECIFIC, ERR_OK, msgid,
                bytearray([self._target_state]))

        elif subcmd == SUBCMD_READ_PP:
            return self._build_response(
                CMD_DEVICE_SPECIFIC, ERR_OK, msgid,
                bytearray(struct.pack('<I', self._pp)))

        elif subcmd == SUBCMD_READ_REG_BY_NAME:
            name = bytes(payload).split(b'\x00')[0].decode('ascii').upper()
            lo, hi = self._registers.get(name, (0, 0))
            resp_payload = bytearray()
            resp_payload.extend(struct.pack('<I', lo))
            resp_payload.extend(struct.pack('<I', hi))
            return self._build_response(
                CMD_DEVICE_SPECIFIC, ERR_OK, msgid, resp_payload)

        elif subcmd == SUBCMD_READ_MEMORY:
            # Payload: [addr:4][access:1][0:1][size:2]
            addr = struct.unpack_from('<I', bytes(payload), 0)[0]
            size = struct.unpack_from('<H', bytes(payload), 6)[0]
            data = bytearray(size)
            for i in range(size):
                data[i] = self._memory.get(addr + i, 0xFF)
            return self._build_response(
                CMD_DEVICE_SPECIFIC, ERR_OK, msgid, data)

        elif subcmd == SUBCMD_WRITE_MEMORY:
            # Payload: [addr:4][access:1][0:1][size:2][data:N]
            addr = struct.unpack_from('<I', bytes(payload), 0)[0]
            size = struct.unpack_from('<H', bytes(payload), 6)[0]
            data = payload[8:8 + size]
            for i in range(size):
                if i < len(data):
                    self._memory[addr + i] = data[i]
            return self._build_response(
                CMD_DEVICE_SPECIFIC, ERR_OK, msgid)

        return self._build_response(CMD_DEVICE_SPECIFIC, ERR_OK, msgid)

    def _build_response(self, cmd, status, msgid, payload=None):
        """Build response data: [flags][header][CMD][status][MSGID][payload].

        This data is what the client receives after stripping the UDP header.
        Maps to: T32_INBUFFER[-1]=flags, [0]=header, [1]=CMD, [2]=status,
                 [3]=MSGID, [4+]=payload
        """
        data = bytearray()
        data.append(0x00)    # flags byte
        data.append(0x00)    # header byte (response LEN analog)
        data.append(cmd)     # CMD echo
        data.append(status)  # status
        data.append(msgid)   # MSGID echo
        if payload:
            data.extend(payload)
        return data

    def _send_response(self, data, addr):
        """Wrap response data in UDP packet and send."""
        packet = bytearray(4 + len(data))
        packet[0] = T32_API_RECEIVE
        packet[1] = 0  # no continuation
        struct.pack_into('<H', packet, 2, self._transmit_seq & 0xFFFF)
        packet[4:] = data
        self._sock.sendto(bytes(packet), addr)
        self._transmit_seq += 1

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
        elif e.startswith('SYMBOL.BEGIN('):
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
            client.connect(host='192.0.2.1', port=1, timeout=0.2)


# ======================================================================
# Test: Client with Mock Server
# ======================================================================

class TestClientWithMockServer(unittest.TestCase):
    """Integration tests using the mock TRACE32 UDP server."""

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
        self.assertEqual(value, 0x20010000)

    def test_read_pc(self):
        self.server.set_pp(0x08002000)
        pc = self.client.read_pc()
        self.assertEqual(pc, 0x08002000)

    def test_eval_expression(self):
        result = self.client.eval_expression("VERSION.SOFTWARE()")
        self.assertEqual(result, "TRACE32 Mock Server")

    def test_get_message(self):
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
            self.assertFalse(client.connected)
        finally:
            server.stop()

    def test_reconnect(self):
        """Disconnect and reconnect."""
        self.client.disconnect()
        self.assertFalse(self.client.connected)
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
# Test: Protocol Message Building (NETASSIST format)
# ======================================================================

class TestProtocolMessages(unittest.TestCase):
    """Test the internal message building of the client."""

    def test_build_msg_basic(self):
        client = Trace32Client()
        msg = client._build_msg(0x73, 0x00)
        # Format: [LEN=2][CMD=0x73][SUBCMD=0x00][MSGID]
        self.assertEqual(len(msg), 4)
        self.assertEqual(msg[0], 2)     # LEN
        self.assertEqual(msg[1], 0x73)  # CMD
        self.assertEqual(msg[2], 0x00)  # SUBCMD

    def test_build_msg_with_payload(self):
        client = Trace32Client()
        payload = bytearray(b'\x01\x02\x03')
        msg = client._build_msg(0x72, 0x02, payload)
        # Format: [LEN=5][CMD=0x72][SUBCMD=0x02][MSGID][0x01][0x02][0x03]
        self.assertEqual(len(msg), 7)
        self.assertEqual(msg[0], 5)     # LEN = 2 + 3
        self.assertEqual(msg[1], 0x72)  # CMD
        self.assertEqual(msg[2], 0x02)  # SUBCMD
        self.assertEqual(msg[4], 0x01)  # payload[0]
        self.assertEqual(msg[5], 0x02)  # payload[1]
        self.assertEqual(msg[6], 0x03)  # payload[2]

    def test_build_msg_with_msg_len_override(self):
        client = Trace32Client()
        payload = bytearray(b'\x01\x02\x03\x04\x05')
        msg = client._build_msg(0x74, 0x31, payload, msg_len=10)
        self.assertEqual(msg[0], 10)    # overridden LEN
        self.assertEqual(msg[1], 0x74)  # CMD

    def test_msg_id_increments(self):
        client = Trace32Client()
        msg1 = client._build_msg(0x73, 0x00)
        msg2 = client._build_msg(0x73, 0x00)
        self.assertEqual(msg2[3], msg1[3] + 1)

    def test_msg_id_wraps_at_256(self):
        client = Trace32Client()
        client._msg_id = 255
        msg = client._build_msg(0x73, 0x00)
        self.assertEqual(msg[3], 255)
        msg2 = client._build_msg(0x73, 0x00)
        self.assertEqual(msg2[3], 0)


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
        """Write and read 256 bytes."""
        block = bytes(bytearray(range(256)))
        self.client.write_memory(0x10000, block)
        data = self.client.read_memory(0x10000, 256)
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
