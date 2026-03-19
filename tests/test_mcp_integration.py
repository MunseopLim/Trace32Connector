#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Integration tests for MCP server with MockTrace32Server.

Tests the full MCP protocol flow (initialize -> connect -> tools -> disconnect)
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
from mcp_server import _handle_request, _core_manager


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


class TestMcpInitialize(unittest.TestCase):
    """Test MCP protocol initialization flow."""

    def test_initialize_returns_server_info(self):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0.0"}
            }
        }
        response = _handle_request(request)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "trace32-mcp-server")

    def test_tools_list_returns_all_tools(self):
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        response = _handle_request(request)
        tools = response["result"]["tools"]
        self.assertGreaterEqual(len(tools), 21)
        names = [t["name"] for t in tools]
        self.assertIn("t32_connect", names)
        self.assertIn("t32_cmd", names)
        self.assertIn("t32_read_memory", names)


class _MockServerTestBase(unittest.TestCase):
    """Base class that starts/stops MockTrace32Server and connects MCP client."""

    @classmethod
    def setUpClass(cls):
        cls._mock = MockTrace32Server()
        cls._mock.set_memory(0x1000, b'\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE')
        cls._mock.set_register('PC', 0x08002000)
        cls._mock.set_register('SP', 0x20008000)
        cls._mock.set_register('R0', 0xDEADBEEF)
        cls._mock.set_register('R1', 0xCAFEBABE)

        t = threading.Thread(target=cls._mock.start)
        t.daemon = True
        t.start()
        time.sleep(0.3)

        # Connect via MCP handler
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


class TestMcpConnect(_MockServerTestBase):
    """Test connection and basic info tools."""

    def test_get_version(self):
        r = _call_tool('t32_get_version', {})
        self.assertFalse(r['is_error'])
        self.assertIn('version', r['parsed'])

    def test_get_state(self):
        r = _call_tool('t32_get_state', {})
        self.assertFalse(r['is_error'])
        self.assertIn('state_name', r['parsed'])
        self.assertEqual(r['parsed']['state_name'], 'stopped')


class TestMcpRegister(_MockServerTestBase):
    """Test register read through MCP."""

    def test_read_pc(self):
        r = _call_tool('t32_read_register', {'name': 'PC'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['value'], 0x08002000)

    def test_read_r0(self):
        r = _call_tool('t32_read_register', {'name': 'R0'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['value'], 0xDEADBEEF)

    def test_read_r1(self):
        r = _call_tool('t32_read_register', {'name': 'R1'})
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['value'], 0xCAFEBABE)


class TestMcpMemory(_MockServerTestBase):
    """Test memory read through MCP."""

    def test_read_memory(self):
        r = _call_tool('t32_read_memory', {
            'address': '0x1000', 'size': 8, 'access': 'D',
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['hex'], 'DEADBEEFCAFEBABE')

    def test_read_memory_size(self):
        r = _call_tool('t32_read_memory', {
            'address': '0x1000', 'size': 4,
        })
        self.assertFalse(r['is_error'])
        self.assertEqual(r['parsed']['size'], 4)
        self.assertEqual(len(r['parsed']['hex']), 8)


class TestMcpEval(_MockServerTestBase):
    """Test expression evaluation through MCP."""

    def test_eval_register(self):
        r = _call_tool('t32_eval', {'expression': 'Register(R0)'})
        self.assertFalse(r['is_error'])
        self.assertIn('0xDEADBEEF', r['parsed']['result'])

    def test_eval_pc(self):
        r = _call_tool('t32_eval', {'expression': 'Register(PC)'})
        self.assertFalse(r['is_error'])
        self.assertIn('0x8002000', r['parsed']['result'])


if __name__ == '__main__':
    unittest.main()
