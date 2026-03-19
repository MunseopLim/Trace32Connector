#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diagnose MockTrace32Server and client connection.

Simple test to verify that the mock server can handle ATTACH.
"""
from __future__ import print_function

import sys
import os
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_client import MockTrace32Server
from t32.client import Trace32Client


def test_mock_server_connection():
    """Test if client can connect to mock server."""
    print("=" * 70)
    print("Test: MockTrace32Server Connection")
    print("=" * 70)

    # Start mock server
    mock_server = MockTrace32Server()
    server_thread = threading.Thread(target=mock_server.start)
    server_thread.daemon = True
    server_thread.start()

    print("\n[1] Mock server started on port {0}".format(mock_server.port))

    # Pre-populate data
    mock_server.set_memory(0x1000, b'\xAA\xBB\xCC\xDD\xEE\xFF\x00\x11')
    mock_server.set_register('PC', 0x08001000)
    mock_server.set_register('SP', 0x20004000)
    print("[2] Pre-populated data:")
    print("    Memory @ 0x1000: AA BB CC DD EE FF 00 11")
    print("    PC: 0x08001000, SP: 0x20004000")

    # Try to connect
    time.sleep(0.5)
    print("\n[3] Creating Trace32Client...")
    client = Trace32Client()

    print("[4] Attempting connect to 127.0.0.1:{0}".format(mock_server.port))
    try:
        client.connect(host='127.0.0.1', port=mock_server.port, timeout=5.0)
        print("[OK] Connection successful!")
    except Exception as e:
        print("[ERROR] Connection failed: {0}".format(str(e)))
        print("\n    Mock server request log: {0}".format(mock_server._request_log))
        mock_server.stop()
        return False

    # Test basic operations
    try:
        print("\n[5] Testing get_state()...")
        state = client.get_state()
        print("[OK] State: {0}".format(state))

        print("\n[6] Testing read_register('PC')...")
        pc = client.read_register('PC')
        print("[OK] PC = 0x{0:X}".format(pc))

        print("\n[7] Testing read_memory(0x1000, 8)...")
        data = client.read_memory(0x1000, 8)
        hex_data = ' '.join(['{0:02X}'.format(b) for b in bytearray(data)])
        print("[OK] Data: {0}".format(hex_data))

        print("\n[8] Testing eval_expression('Register(PC)')...")
        result = client.eval_expression('Register(PC)')
        print("[OK] Result: {0}".format(result))

        print("\n" + "=" * 70)
        print("All tests passed!")
        print("=" * 70)

    except Exception as e:
        print("[ERROR] Test failed: {0}".format(str(e)))
        print("\n    Mock server request log: {0}".format(mock_server._request_log))
        return False
    finally:
        client.disconnect()
        mock_server.stop()

    return True


if __name__ == '__main__':
    success = test_mock_server_connection()
    sys.exit(0 if success else 1)
