#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Advanced integration tests for MCP server.

Tests breakpoints, memory write, execution control, commands, etc.
using MockTrace32Server as the backend.
"""
from __future__ import print_function

import json
import threading
import time
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.test_client import MockTrace32Server
from mcp_server import _handle_request, _client


def _call_tool(tool_name, arguments):
    """Helper: send a tools/call request and return parsed result."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    response = _handle_request(request)
    result = response.get('result', {})
    is_error = result.get('isError', False)
    content_list = result.get('content', [{}])
    text = content_list[0].get('text', '') if content_list else ''
    parsed = None
    if text and not is_error:
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            pass
    return {
        'response': response,
        'is_error': is_error,
        'text': text,
        'parsed': parsed,
    }


class _MockServerTestBase(unittest.TestCase):
    """Base class that starts/stops MockTrace32Server and connects MCP client."""

    @classmethod
    def setUpClass(cls):
        cls._mock = MockTrace32Server()
        cls._mock.set_memory(0x08000000, b'\x00\x00\x00\x00\x00\x00\x00\x00')
        cls._mock.set_memory(0x1000, b'\xAA\xBB\xCC\xDD')
        cls._mock.set_register('PC', 0x08001000)
        cls._mock.set_register('SP', 0x20008000)
        cls._mock.set_register('R0', 0x12345678)
        cls._mock.set_register('R1', 0x87654321)
        cls._mock.set_register('LR', 0x08002000)
        cls._mock.set_register('CPSR', 0x60000010)

        t = threading.Thread(target=cls._mock.start)
        t.daemon = True
        t.start()
        time.sleep(0.3)

        result = _call_tool('t32_connect', {
            'host': '127.0.0.1',
            'port': cls._mock.port,
        })
        if result['is_error']:
            raise RuntimeError(
                "setUp connect failed: {0}".format(result['text'])
            )

    @classmethod
    def tearDownClass(cls):
        _call_tool('t32_disconnect', {})
        cls._mock.stop()


class TestMemoryWrite(_MockServerTestBase):
    """Test memory write operations."""

    def test_write_4_bytes(self):
        r = _call_tool('t32_write_memory', {
            'address': '0x20004000',
            'data': 'DEADBEEF',
            'access': 'D',
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['bytes_written'], 4)

    def test_write_8_bytes(self):
        r = _call_tool('t32_write_memory', {
            'address': '0x20005000',
            'data': 'CAFEBABECAFEBABE',
            'access': 'D',
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['bytes_written'], 8)


class TestBreakpoints(_MockServerTestBase):
    """Test breakpoint operations."""

    def test_set_breakpoint(self):
        r = _call_tool('t32_breakpoint_set', {
            'address': '0x08001000',
            'type': 'program',
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['status'], 'ok')

    def test_set_second_breakpoint(self):
        r = _call_tool('t32_breakpoint_set', {
            'address': '0x08002000',
            'type': 'program',
        })
        self.assertFalse(r['is_error'])

    def test_list_breakpoints(self):
        r = _call_tool('t32_breakpoint_list', {})
        self.assertFalse(r['is_error'])

    def test_delete_breakpoint(self):
        r = _call_tool('t32_breakpoint_delete', {
            'address': '0x08001000',
        })
        self.assertFalse(r['is_error'])

    def test_delete_all_breakpoints(self):
        r = _call_tool('t32_breakpoint_delete', {})
        self.assertFalse(r['is_error'])


class TestExecutionControl(_MockServerTestBase):
    """Test target execution control."""

    def test_break_target(self):
        r = _call_tool('t32_break', {})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['action'], 'target halted')

    def test_go_target(self):
        r = _call_tool('t32_go', {})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['action'], 'target execution started')

    def test_single_step(self):
        r = _call_tool('t32_step', {'count': 1})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['steps'], 1)

    def test_multi_step(self):
        r = _call_tool('t32_step', {'count': 10})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['steps'], 10)


class TestCommands(_MockServerTestBase):
    """Test TRACE32 PRACTICE command execution."""

    def test_system_up(self):
        r = _call_tool('t32_cmd', {'command': 'SYStem.Up'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['command'], 'SYStem.Up')

    def test_break_set(self):
        r = _call_tool('t32_cmd', {'command': 'Break.Set main'})
        self.assertFalse(r['is_error'])


class TestExpressionEval(_MockServerTestBase):
    """Test expression evaluation."""

    def test_eval_register_pc(self):
        r = _call_tool('t32_eval', {'expression': 'Register(PC)'})
        self.assertFalse(r['is_error'])
        self.assertIn('0x8001000', r['parsed']['result'])

    def test_eval_register_r0(self):
        r = _call_tool('t32_eval', {'expression': 'Register(R0)'})
        self.assertFalse(r['is_error'])
        self.assertIn('0x12345678', r['parsed']['result'])

    def test_eval_register_r1(self):
        r = _call_tool('t32_eval', {'expression': 'Register(R1)'})
        self.assertFalse(r['is_error'])
        self.assertIn('0x87654321', r['parsed']['result'])


class TestStateInspection(_MockServerTestBase):
    """Test state and version inspection."""

    def test_get_state(self):
        r = _call_tool('t32_get_state', {})
        self.assertFalse(r['is_error'])
        self.assertIn('state_name', r['parsed'])

    def test_get_version(self):
        r = _call_tool('t32_get_version', {})
        self.assertFalse(r['is_error'])
        self.assertIn('version', r['parsed'])


if __name__ == '__main__':
    unittest.main()
