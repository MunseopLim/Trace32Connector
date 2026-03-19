#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Integration test for MCP server with MockTrace32Server.

This test directly calls MCP request handlers with a mock TRACE32 backend,
avoiding subprocess/IPC issues.
"""
from __future__ import print_function

import sys
import os
import json
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_client import MockTrace32Server
from mcp_server import _handle_request, TOOLS, _client


def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def print_request_response(req_id, method, params, response):
    """Pretty-print a request/response pair."""
    print("\n[Request ID: {0}]".format(req_id))
    print("  Method: {0}".format(method))
    if params:
        print("  Params: {0}".format(json.dumps(params, indent=2)[:100]))
    if response:
        result = response.get('result', {})
        if isinstance(result, dict) and 'content' in result:
            content = result['content']
            if isinstance(content, list) and content:
                text = content[0].get('text', '')[:80]
                is_error = result.get('isError', False)
                status = "ERROR" if is_error else "OK"
                print("  Response [{0}]: {1}".format(status, text))
        else:
            print("  Response: {0}".format(json.dumps(result, indent=2)[:100]))


def test_mcp_with_mock_server():
    """Test MCP handlers against mock TRACE32 server."""
    print_section("MCP Integration Test with MockTrace32Server")

    # Start mock server
    print("\n[SETUP] Starting MockTrace32Server...")
    mock_server = MockTrace32Server()
    server_thread = threading.Thread(target=mock_server.start)
    server_thread.daemon = True
    server_thread.start()
    time.sleep(0.3)

    print("[SETUP] Mock server running on port {0}".format(mock_server.port))

    # Pre-populate data
    mock_server.set_memory(0x1000, b'\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE')
    mock_server.set_register('PC', 0x08002000)
    mock_server.set_register('SP', 0x20008000)
    mock_server.set_register('R0', 0xDEADBEEF)
    mock_server.set_register('R1', 0xCAFEBABE)
    print("[SETUP] Memory & registers pre-populated")

    # Test sequence
    test_cases = [
        # Step 1: Initialize
        {
            "name": "Initialize",
            "request": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0.0"}
                }
            }
        },
        # Step 2: List tools
        {
            "name": "List Tools",
            "request": {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
        },
        # Step 3: Connect to mock server
        {
            "name": "Connect to MockTrace32",
            "request": {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "t32_connect",
                    "arguments": {
                        "host": "127.0.0.1",
                        "port": mock_server.port
                    }
                }
            }
        },
        # Step 4: Get version
        {
            "name": "Get Version",
            "request": {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "t32_get_version",
                    "arguments": {}
                }
            }
        },
        # Step 5: Get state
        {
            "name": "Get Target State",
            "request": {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "t32_get_state",
                    "arguments": {}
                }
            }
        },
        # Step 6: Read register
        {
            "name": "Read Register (PC)",
            "request": {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "t32_read_register",
                    "arguments": {"name": "PC"}
                }
            }
        },
        # Step 7: Read another register
        {
            "name": "Read Register (R0)",
            "request": {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "t32_read_register",
                    "arguments": {"name": "R0"}
                }
            }
        },
        # Step 8: Read memory
        {
            "name": "Read Memory (0x1000, 8 bytes)",
            "request": {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "t32_read_memory",
                    "arguments": {
                        "address": "0x1000",
                        "size": 8,
                        "access": "D"
                    }
                }
            }
        },
        # Step 9: Evaluate expression
        {
            "name": "Evaluate Expression",
            "request": {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "t32_eval",
                    "arguments": {
                        "expression": "Register(R0)"
                    }
                }
            }
        },
        # Step 10: Disconnect
        {
            "name": "Disconnect",
            "request": {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "t32_disconnect",
                    "arguments": {}
                }
            }
        },
    ]

    print_section("Executing Test Cases")
    results = []

    for i, test_case in enumerate(test_cases, 1):
        test_name = test_case["name"]
        request = test_case["request"]

        try:
            response = _handle_request(request)
            is_error = False

            # Check if response has error
            if response and 'result' in response:
                result = response['result']
                if isinstance(result, dict) and result.get('isError'):
                    is_error = True

            status = "FAIL" if is_error else "PASS"
            print("[{0}/{1}] {2}... {3}".format(i, len(test_cases), test_name, status))
            print_request_response(
                request.get('id'),
                request.get('method'),
                request.get('params'),
                response
            )
            results.append((test_name, status, response))

            if is_error and i < len(test_cases):
                # Stop on error unless it's the last test
                if i > 3:  # Allow to proceed past connection attempts
                    print("\n[WARNING] Stopping due to error")
                    break

        except Exception as e:
            print("[{0}/{1}] {2}... EXCEPTION".format(i, len(test_cases), test_name))
            print("  Error: {0}".format(str(e)))
            results.append((test_name, "EXCEPTION", str(e)))
            break

        time.sleep(0.2)

    # Cleanup
    print_section("Cleanup")
    print("[CLEANUP] Stopping MockTrace32Server...")
    mock_server.stop()

    # Summary
    print_section("Test Summary")
    passed = sum(1 for _, status, _ in results if status == "PASS")
    failed = sum(1 for _, status, _ in results if status == "FAIL")
    exceptions = sum(1 for _, status, _ in results if status == "EXCEPTION")

    print("Results: {0} passed, {1} failed, {2} exceptions".format(passed, failed, exceptions))
    print("\nDetails:")
    for name, status, _ in results:
        print("  - {0}: {1}".format(name, status))

    print_section("End of Test")
    return failed == 0 and exceptions == 0


if __name__ == '__main__':
    success = test_mcp_with_mock_server()
    sys.exit(0 if success else 1)
