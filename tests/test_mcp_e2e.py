#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""End-to-end tests for MCP server via subprocess stdin/stdout.

Tests the REAL MCP server process (not just _handle_request),
communicating over JSON-RPC through stdin/stdout pipes,
backed by MockTrace32Server.

This simulates exactly how an AI client (Claude, etc.) talks to the MCP server.
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.test_client import MockTrace32Server


class McpSubprocess(object):
    """Manages an MCP server subprocess with stdin/stdout pipes."""

    def __init__(self):
        self._proc = None
        self._req_id = 0

    def start(self):
        """Launch mcp_server.py as a subprocess."""
        server_path = os.path.join(
            os.path.dirname(__file__), '..', 'mcp_server.py'
        )
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        self._proc = subprocess.Popen(
            [sys.executable, server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Give server a moment to start
        time.sleep(0.3)
        if self._proc.poll() is not None:
            err = self._proc.stderr.read().decode('utf-8', errors='replace')
            raise RuntimeError("MCP server failed to start: {0}".format(err))

    def stop(self):
        """Terminate the MCP server subprocess."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()

    def send(self, method, params=None):
        """Send a JSON-RPC request and return the parsed response.

        Args:
            method: JSON-RPC method name
            params: Optional params dict

        Returns:
            Parsed JSON response dict, or None on failure.
        """
        self._req_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(request, ensure_ascii=True) + '\n'
        self._proc.stdin.write(
            line.encode('utf-8') if isinstance(line, str) else line
        )
        self._proc.stdin.flush()

        # Read lines until we get the actual response (skip notifications)
        while True:
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                return None
            parsed = json.loads(resp_line.decode('utf-8'))
            # Notifications have no 'id'; skip them
            if 'id' in parsed:
                return parsed

    def call_tool(self, tool_name, arguments=None):
        """Convenience: send a tools/call request.

        Returns:
            Dict with 'is_error', 'text', 'parsed' keys.
        """
        response = self.send('tools/call', {
            'name': tool_name,
            'arguments': arguments or {},
        })
        if response is None:
            return {'is_error': True, 'text': 'No response', 'parsed': None}

        result = response.get('result', {})
        is_error = result.get('isError', False)
        content_list = result.get('content', [{}])
        text = content_list[0].get('text', '') if content_list else ''
        parsed = None
        if text and not is_error:
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = text
        return {
            'is_error': is_error,
            'text': text,
            'parsed': parsed,
        }


class TestMcpE2E(unittest.TestCase):
    """End-to-end test: MockTrace32Server <-> mcp_server.py subprocess <-> JSON-RPC."""

    @classmethod
    def setUpClass(cls):
        # Start mock TRACE32 server
        cls._mock = MockTrace32Server()
        cls._mock.set_memory(0x1000, b'\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE')
        cls._mock.set_memory(0x2000, b'\x01\x02\x03\x04')
        cls._mock.set_register('PC', 0x08001000)
        cls._mock.set_register('SP', 0x20008000)
        cls._mock.set_register('R0', 0xAABBCCDD)
        cls._mock.set_register('LR', 0x08002000)

        t = threading.Thread(target=cls._mock.start)
        t.daemon = True
        t.start()
        time.sleep(0.3)

        # Start MCP server subprocess
        cls._mcp = McpSubprocess()
        cls._mcp.start()

    @classmethod
    def tearDownClass(cls):
        # Disconnect and stop
        try:
            cls._mcp.call_tool('t32_disconnect')
        except Exception:
            pass
        cls._mcp.stop()
        cls._mock.stop()

    # -- Protocol tests --

    def test_01_initialize(self):
        """MCP initialize handshake should return server info."""
        resp = self._mcp.send('initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'e2e-test', 'version': '1.0.0'},
        })
        self.assertIsNotNone(resp)
        result = resp['result']
        self.assertEqual(result['protocolVersion'], '2024-11-05')
        self.assertEqual(result['serverInfo']['name'], 'trace32-mcp-server')

    def test_02_tools_list(self):
        """tools/list should return all 21 tools."""
        resp = self._mcp.send('tools/list')
        self.assertIsNotNone(resp)
        tools = resp['result']['tools']
        self.assertGreaterEqual(len(tools), 21)
        tool_names = [t['name'] for t in tools]
        for expected in ['t32_connect', 't32_cmd', 't32_read_memory',
                         't32_read_register', 't32_breakpoint_set']:
            self.assertIn(expected, tool_names)

    def test_03_ping(self):
        """ping should return empty result."""
        resp = self._mcp.send('ping')
        self.assertIsNotNone(resp)
        self.assertIn('result', resp)

    def test_04_unknown_method(self):
        """Unknown method should return JSON-RPC error."""
        resp = self._mcp.send('nonexistent/method')
        self.assertIsNotNone(resp)
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)

    # -- Connection tests --

    def test_05_tool_without_connection(self):
        """Calling a tool before connecting should return an error."""
        r = self._mcp.call_tool('t32_get_state')
        self.assertTrue(r['is_error'])
        self.assertIn('not connected', r['text'].lower())

    def test_06_connect(self):
        """Connect to MockTrace32Server via MCP."""
        r = self._mcp.call_tool('t32_connect', {
            'host': '127.0.0.1',
            'port': self._mock.port,
        })
        self.assertFalse(r['is_error'], "Connect failed: {0}".format(r['text']))
        self.assertEqual(r['parsed']['status'], 'connected')

    # -- Functional tests (require connection) --

    def test_07_get_state(self):
        """Get target state after connection."""
        r = self._mcp.call_tool('t32_get_state')
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['state_name'], 'stopped')

    def test_08_get_version(self):
        """Get TRACE32 version."""
        r = self._mcp.call_tool('t32_get_version')
        self.assertFalse(r['is_error'])
        self.assertIn('version', r['parsed'])

    def test_09_read_register_pc(self):
        """Read PC register."""
        r = self._mcp.call_tool('t32_read_register', {'name': 'PC'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['value'], 0x08001000)
        self.assertEqual(r['parsed']['hex'], '0x8001000')

    def test_10_read_register_r0(self):
        """Read R0 register."""
        r = self._mcp.call_tool('t32_read_register', {'name': 'R0'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['value'], 0xAABBCCDD)

    def test_11_read_memory(self):
        """Read 8 bytes from address 0x1000."""
        r = self._mcp.call_tool('t32_read_memory', {
            'address': '0x1000',
            'size': 8,
            'access': 'D',
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['hex'], 'DEADBEEFCAFEBABE')

    def test_12_read_memory_partial(self):
        """Read 4 bytes from address 0x2000."""
        r = self._mcp.call_tool('t32_read_memory', {
            'address': '0x2000',
            'size': 4,
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['hex'], '01020304')
        self.assertEqual(r['parsed']['size'], 4)

    def test_13_write_memory(self):
        """Write memory and verify."""
        r = self._mcp.call_tool('t32_write_memory', {
            'address': '0x3000',
            'data': 'AABBCCDD',
            'access': 'D',
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['bytes_written'], 4)

    def test_14_eval_expression(self):
        """Evaluate Register(PC) expression."""
        r = self._mcp.call_tool('t32_eval', {
            'expression': 'Register(PC)',
        })
        self.assertFalse(r['is_error'])
        self.assertIn('0x8001000', r['parsed']['result'])

    def test_15_cmd_system_up(self):
        """Execute SYStem.Up command."""
        r = self._mcp.call_tool('t32_cmd', {'command': 'SYStem.Up'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['command'], 'SYStem.Up')

    def test_16_breakpoint_set(self):
        """Set a program breakpoint."""
        r = self._mcp.call_tool('t32_breakpoint_set', {
            'address': '0x08001000',
            'type': 'program',
        })
        self.assertFalse(r['is_error'])

    def test_17_breakpoint_list(self):
        """List breakpoints."""
        r = self._mcp.call_tool('t32_breakpoint_list')
        self.assertFalse(r['is_error'])

    def test_18_breakpoint_delete_all(self):
        """Delete all breakpoints."""
        r = self._mcp.call_tool('t32_breakpoint_delete')
        self.assertFalse(r['is_error'])

    def test_19_go(self):
        """Resume target execution."""
        r = self._mcp.call_tool('t32_go')
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['action'], 'target execution started')

    def test_20_break(self):
        """Halt target execution."""
        r = self._mcp.call_tool('t32_break')
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['action'], 'target halted')

    def test_21_step(self):
        """Single step."""
        r = self._mcp.call_tool('t32_step', {'count': 1})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['steps'], 1)

    def test_22_step_multiple(self):
        """Multi-step (5 steps)."""
        r = self._mcp.call_tool('t32_step', {'count': 5})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['steps'], 5)

    def test_23_unknown_tool(self):
        """Unknown tool should return isError."""
        r = self._mcp.call_tool('nonexistent_tool')
        self.assertTrue(r['is_error'])
        self.assertIn('Unknown tool', r['text'])

    def test_24_disconnect(self):
        """Disconnect from TRACE32."""
        r = self._mcp.call_tool('t32_disconnect')
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['status'], 'disconnected')

    def test_25_after_disconnect_returns_error(self):
        """After disconnect, tool calls should fail."""
        r = self._mcp.call_tool('t32_get_state')
        self.assertTrue(r['is_error'])
        self.assertIn('not connected', r['text'].lower())

    def test_26_reconnect(self):
        """Reconnect should work after disconnect."""
        r = self._mcp.call_tool('t32_connect', {
            'host': '127.0.0.1',
            'port': self._mock.port,
        })
        self.assertFalse(r['is_error'], "Reconnect failed: {0}".format(r['text']))
        self.assertEqual(r['parsed']['status'], 'connected')

        # Verify it works
        r = self._mcp.call_tool('t32_get_state')
        self.assertFalse(r['is_error'])


if __name__ == '__main__':
    unittest.main()
