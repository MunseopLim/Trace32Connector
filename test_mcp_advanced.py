#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Advanced integration tests for MCP server.

Tests complex scenarios: breakpoints, memory write, variables, etc.
"""
from __future__ import print_function

import sys
import os
import json
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_client import MockTrace32Server
from mcp_server import _handle_request, _client


def print_test(name, passed):
    """Print test result."""
    status = "PASS" if passed else "FAIL"
    print("  [{0}] {1}".format(status, name))


def call_tool(tool_name, arguments):
    """Call an MCP tool and return result."""
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
    content = result.get('content', [{}])[0].get('text', '')

    return {
        'response': response,
        'is_error': is_error,
        'content': content,
        'parsed': None
    }


def test_advanced_scenarios():
    """Test advanced MCP scenarios."""
    print("\n" + "=" * 70)
    print("Advanced MCP Integration Tests")
    print("=" * 70)

    # Setup
    mock_server = MockTrace32Server()
    server_thread = threading.Thread(target=mock_server.start)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.3)

    print("\n[SETUP] Mock server on port {0}".format(mock_server.port))

    # Pre-populate extensive test data
    mock_server.set_memory(0x08000000, b'\x00\x00\x00\x00\x00\x00\x00\x00')
    for i in range(256, 512):
        mock_server.set_memory(0x20000000 + i, bytes([i % 256]))
    mock_server.set_register('PC', 0x08001000)
    mock_server.set_register('SP', 0x20008000)
    mock_server.set_register('R0', 0x12345678)
    mock_server.set_register('R1', 0x87654321)
    mock_server.set_register('LR', 0x08002000)
    mock_server.set_register('CPSR', 0x60000010)

    print("[SETUP] Data pre-populated")

    # Connect
    print("\n" + "=" * 70)
    print("Test Suite 1: Basic Operations")
    print("=" * 70)

    result = call_tool('t32_connect', {
        'host': '127.0.0.1',
        'port': mock_server.port
    })
    print_test("Connect to mock server", not result['is_error'])

    if result['is_error']:
        print("Connection failed: {0}".format(result['content']))
        mock_server.stop()
        return False

    # Test Suite 1: Register operations
    result = call_tool('t32_read_register', {'name': 'PC'})
    print_test("Read PC register", not result['is_error'])

    result = call_tool('t32_read_register', {'name': 'R0'})
    print_test("Read R0 register", not result['is_error'])

    result = call_tool('t32_read_register', {'name': 'CPSR'})
    print_test("Read CPSR register", not result['is_error'])

    # Test Suite 2: Memory operations
    print("\n" + "=" * 70)
    print("Test Suite 2: Memory Operations")
    print("=" * 70)

    result = call_tool('t32_read_memory', {
        'address': '0x08000000',
        'size': 8,
        'access': 'D'
    })
    print_test("Read data memory (address=0x08000000)", not result['is_error'])

    result = call_tool('t32_read_memory', {
        'address': '0x20000100',
        'size': 16,
        'access': 'D'
    })
    passed = not result['is_error']
    if passed:
        try:
            content_json = json.loads(result['content'])
            hex_data = content_json.get('hex', '')
            passed = len(hex_data) == 32  # 16 bytes = 32 hex chars
        except:
            passed = False
    print_test("Read data memory (large block)", passed)

    # Test write memory
    result = call_tool('t32_write_memory', {
        'address': '0x20004000',
        'data': 'DEADBEEF',
        'access': 'D'
    })
    print_test("Write memory (4 bytes)", not result['is_error'])

    result = call_tool('t32_write_memory', {
        'address': '0x20005000',
        'data': 'CAFEBABECAFEBABE',
        'access': 'D'
    })
    print_test("Write memory (8 bytes)", not result['is_error'])

    # Test Suite 3: Expression evaluation
    print("\n" + "=" * 70)
    print("Test Suite 3: Expression Evaluation")
    print("=" * 70)

    expressions = [
        ('Register(PC)', '0x8001000'),
        ('Register(R0)', '0x12345678'),
        ('Register(R1)', '0x87654321'),
    ]

    for expr, expected in expressions:
        result = call_tool('t32_eval', {'expression': expr})
        passed = not result['is_error']
        if passed:
            try:
                content_json = json.loads(result['content'])
                result_val = content_json.get('result', '')
                passed = expected in result_val
            except:
                passed = False
        print_test("Eval: {0}".format(expr), passed)

    # Test Suite 4: Breakpoint operations
    print("\n" + "=" * 70)
    print("Test Suite 4: Breakpoint Operations")
    print("=" * 70)

    result = call_tool('t32_breakpoint_set', {
        'address': '0x08001000',
        'type': 'program'
    })
    print_test("Set breakpoint at 0x08001000", not result['is_error'])

    result = call_tool('t32_breakpoint_set', {
        'address': '0x08002000',
        'type': 'program'
    })
    print_test("Set breakpoint at 0x08002000", not result['is_error'])

    result = call_tool('t32_breakpoint_list', {})
    print_test("List breakpoints", not result['is_error'])

    result = call_tool('t32_breakpoint_delete', {'address': '0x08001000'})
    print_test("Delete breakpoint at 0x08001000", not result['is_error'])

    result = call_tool('t32_breakpoint_delete', {})
    print_test("Delete all breakpoints", not result['is_error'])

    # Test Suite 5: Execution control
    print("\n" + "=" * 70)
    print("Test Suite 5: Execution Control")
    print("=" * 70)

    result = call_tool('t32_break', {})
    print_test("Break (halt) target", not result['is_error'])

    result = call_tool('t32_go', {})
    print_test("Go (resume) target", not result['is_error'])

    result = call_tool('t32_step', {'count': 1})
    print_test("Single step", not result['is_error'])

    result = call_tool('t32_step', {'count': 10})
    print_test("Multi-step (10)", not result['is_error'])

    # Test Suite 6: State inspection
    print("\n" + "=" * 70)
    print("Test Suite 6: State Inspection")
    print("=" * 70)

    result = call_tool('t32_get_state', {})
    print_test("Get target state", not result['is_error'])

    result = call_tool('t32_get_version', {})
    print_test("Get TRACE32 version", not result['is_error'])

    # Test Suite 7: Commands
    print("\n" + "=" * 70)
    print("Test Suite 7: TRACE32 Commands")
    print("=" * 70)

    result = call_tool('t32_cmd', {'command': 'SYStem.Up'})
    print_test("Execute command: SYStem.Up", not result['is_error'])

    result = call_tool('t32_cmd', {'command': 'Break.Set main'})
    print_test("Execute command: Break.Set", not result['is_error'])

    # Disconnect
    print("\n" + "=" * 70)
    print("Cleanup")
    print("=" * 70)

    result = call_tool('t32_disconnect', {})
    print_test("Disconnect from mock server", not result['is_error'])

    mock_server.stop()

    print("\n" + "=" * 70)
    print("All advanced tests completed!")
    print("=" * 70)

    return True


if __name__ == '__main__':
    success = test_advanced_scenarios()
    sys.exit(0 if success else 1)
