#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MCP Server simulation script for testing without TRACE32.

This script:
1. Starts a mock TRACE32 TCP server
2. Starts the MCP server as a subprocess
3. Sends JSON-RPC requests to simulate MCP client interactions
4. Captures and displays results

Usage:
    python simulate_mcp.py
"""
from __future__ import print_function

import sys
import os
import json
import subprocess
import threading
import time
import socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_client import MockTrace32Server


def run_mock_server(server_instance):
    """Start and run the mock TRACE32 server."""
    server_instance.start()
    print("[MOCK] Mock TRACE32 server started on port {0}".format(server_instance.port))
    print("[MOCK] Pre-populating memory and registers...")

    # Pre-populate some test data
    server_instance.set_memory(0x1000, b'\x01\x02\x03\x04\x05\x06\x07\x08')
    server_instance.set_register('PC', 0x08001000)
    server_instance.set_register('SP', 0x20004000)
    server_instance.set_register('R0', 0x12345678)
    print("[MOCK] Memory: 0x1000 = 01 02 03 04 05 06 07 08")
    print("[MOCK] Registers: PC=0x08001000, SP=0x20004000, R0=0x12345678")

    # Keep server alive and log requests
    import time as time_mod
    for i in range(100):
        time_mod.sleep(0.1)
        if server_instance._request_log:
            print("[MOCK] Received {0} requests: {1}".format(
                len(server_instance._request_log),
                server_instance._request_log
            ))


def start_mcp_server(host, port):
    """Start the MCP server subprocess."""
    print("[MCP] Starting MCP server...")
    mcp_env = os.environ.copy()
    mcp_env['PYTHONUNBUFFERED'] = '1'

    proc = subprocess.Popen(
        [sys.executable, 'mcp_server.py', '--host', host, '--port', str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=mcp_env
    )
    time.sleep(0.5)  # Give server time to start
    print("[MCP] MCP server started (PID: {0})".format(proc.pid))
    return proc


def send_request(proc, request):
    """Send a JSON-RPC request to MCP server and read response."""
    request_line = json.dumps(request, ensure_ascii=True) + '\n'
    print("\n[REQUEST] {0}".format(json.dumps(request, indent=2)))

    try:
        proc.stdin.write(request_line.encode('utf-8') if isinstance(request_line, str) else request_line)
        proc.stdin.flush()

        # Read response line
        response_line = proc.stdout.readline()
        if response_line:
            response = json.loads(response_line.decode('utf-8'))
            print("[RESPONSE] {0}".format(json.dumps(response, indent=2)))
            return response
        else:
            print("[ERROR] No response from MCP server")
            return None
    except Exception as e:
        print("[ERROR] {0}".format(str(e)))
        return None


def main():
    """Run the simulation."""
    print("=" * 70)
    print("TRACE32 MCP Server Simulation")
    print("=" * 70)

    # Start mock TRACE32 server
    mock_server = MockTrace32Server()
    mock_thread = threading.Thread(target=run_mock_server, args=(mock_server,))
    mock_thread.daemon = True
    mock_thread.start()
    time.sleep(0.5)

    # Start MCP server
    mock_host = '127.0.0.1'
    mock_port = mock_server.port
    mcp_proc = start_mcp_server(mock_host, mock_port)
    time.sleep(1.0)

    # Check if server is still running
    if mcp_proc.poll() is not None:
        print("[ERROR] MCP server failed to start")
        print(mcp_proc.stderr.read().decode('utf-8'))
        return 1

    try:
        # Test sequence
        requests = [
            # 1. Initialize
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "simulator", "version": "1.0.0"}
                }
            },
            # 2. List tools
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            },
            # 3. Connect to mock TRACE32
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "t32_connect",
                    "arguments": {"host": mock_host, "port": mock_port}
                }
            },
            # 4. Get version
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "t32_get_version",
                    "arguments": {}
                }
            },
            # 5. Get state
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "t32_get_state",
                    "arguments": {}
                }
            },
            # 6. Read register
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "t32_read_register",
                    "arguments": {"name": "PC"}
                }
            },
            # 7. Read memory
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "t32_read_memory",
                    "arguments": {"address": "0x1000", "size": 8}
                }
            },
            # 8. Evaluate expression
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "t32_eval",
                    "arguments": {"expression": "Register(PC)"}
                }
            },
            # 9. Disconnect
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "t32_disconnect",
                    "arguments": {}
                }
            },
        ]

        print("\n" + "=" * 70)
        print("Sending test requests to MCP server")
        print("=" * 70)

        for request in requests:
            response = send_request(mcp_proc, request)
            if response is None:
                print("[WARNING] Request {0} failed".format(request.get('id')))
                break
            time.sleep(0.2)

        print("\n" + "=" * 70)
        print("Simulation complete!")
        print("=" * 70)

        return 0

    finally:
        # Cleanup
        print("\n[CLEANUP] Stopping MCP server...")
        mcp_proc.terminate()
        try:
            mcp_proc.wait(timeout=2.0)
        except:
            mcp_proc.kill()

        print("[CLEANUP] Stopping mock TRACE32 server...")
        mock_server.stop()


if __name__ == '__main__':
    sys.exit(main())
