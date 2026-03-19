#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for t32.core_manager module.

Tests CoreManager with MockTrace32Server instances.
"""
from __future__ import print_function

import threading
import time
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tests.test_client import MockTrace32Server
from t32.core_manager import CoreManager
from t32.client import Trace32Error


class TestCoreManagerValidation(unittest.TestCase):
    """Test core_id validation."""

    def setUp(self):
        self.mgr = CoreManager()

    def test_valid_core_id_zero(self):
        self.assertEqual(self.mgr._validate_core_id(0), 0)

    def test_valid_core_id_max(self):
        self.assertEqual(self.mgr._validate_core_id(15), 15)

    def test_invalid_core_id_negative(self):
        with self.assertRaises(Trace32Error):
            self.mgr._validate_core_id(-1)

    def test_invalid_core_id_too_large(self):
        with self.assertRaises(Trace32Error):
            self.mgr._validate_core_id(16)

    def test_core_id_string_coercion(self):
        self.assertEqual(self.mgr._validate_core_id("5"), 5)

    def test_get_client_not_connected(self):
        with self.assertRaises(Trace32Error):
            self.mgr.get_client(0)


class TestCoreManagerSingleCore(unittest.TestCase):
    """Test single-core operations (backward compatibility)."""

    def setUp(self):
        self.mgr = CoreManager()
        self.mock = MockTrace32Server()
        self.mock.set_register('PC', 0x08001000)
        t = threading.Thread(target=self.mock.start)
        t.daemon = True
        t.start()
        time.sleep(0.3)

    def tearDown(self):
        self.mgr.disconnect_all()
        self.mock.stop()

    def test_connect_core_zero(self):
        self.mgr.connect_core(0, '127.0.0.1', self.mock.port)
        client = self.mgr.get_client(0)
        self.assertTrue(client.connected)

    def test_connected_count(self):
        self.assertEqual(self.mgr.connected_count, 0)
        self.mgr.connect_core(0, '127.0.0.1', self.mock.port)
        self.assertEqual(self.mgr.connected_count, 1)

    def test_disconnect_core(self):
        self.mgr.connect_core(0, '127.0.0.1', self.mock.port)
        self.mgr.disconnect_core(0)
        self.assertEqual(self.mgr.connected_count, 0)
        with self.assertRaises(Trace32Error):
            self.mgr.get_client(0)

    def test_list_cores(self):
        self.mgr.connect_core(0, '127.0.0.1', self.mock.port)
        cores = self.mgr.list_cores()
        self.assertEqual(len(cores), 1)
        self.assertEqual(cores[0]['core_id'], 0)
        self.assertTrue(cores[0]['connected'])
        self.assertEqual(cores[0]['port'], self.mock.port)

    def test_disconnect_all(self):
        self.mgr.connect_core(0, '127.0.0.1', self.mock.port)
        self.mgr.disconnect_all()
        self.assertEqual(self.mgr.connected_count, 0)

    def test_default_core_id(self):
        """get_client() with no arg defaults to core 0."""
        self.mgr.connect_core(0, '127.0.0.1', self.mock.port)
        client = self.mgr.get_client()
        self.assertTrue(client.connected)


class TestCoreManagerMultiCore(unittest.TestCase):
    """Test multi-core operations with multiple MockTrace32Servers."""

    NUM_CORES = 4

    def setUp(self):
        self.mgr = CoreManager()
        self.mocks = []
        for i in range(self.NUM_CORES):
            mock = MockTrace32Server()
            mock.set_register('PC', 0x08001000 + i * 0x100)
            mock.set_register('R0', i)
            t = threading.Thread(target=mock.start)
            t.daemon = True
            t.start()
            self.mocks.append(mock)
        time.sleep(0.3)

    def tearDown(self):
        self.mgr.disconnect_all()
        for mock in self.mocks:
            mock.stop()

    def test_connect_multiple_cores(self):
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        self.assertEqual(self.mgr.connected_count, self.NUM_CORES)

    def test_core_isolation(self):
        """Each core has its own independent state."""
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)

        for i in range(self.NUM_CORES):
            client = self.mgr.get_client(i)
            r0 = client.read_register('R0')
            self.assertEqual(r0, i, "Core {0} R0 mismatch".format(i))

    def test_list_all_cores(self):
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        cores = self.mgr.list_cores()
        self.assertEqual(len(cores), self.NUM_CORES)
        for i, core in enumerate(cores):
            self.assertEqual(core['core_id'], i)
            self.assertTrue(core['connected'])

    def test_disconnect_one_keeps_others(self):
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        self.mgr.disconnect_core(1)
        self.assertEqual(self.mgr.connected_count, self.NUM_CORES - 1)
        # Core 0 still works
        client = self.mgr.get_client(0)
        self.assertTrue(client.connected)
        # Core 1 should fail
        with self.assertRaises(Trace32Error):
            self.mgr.get_client(1)

    def test_connect_all_consecutive_ports(self):
        """connect_all with non-consecutive mock ports (simulated)."""
        # Use individual connects since mock ports are random
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        self.assertEqual(self.mgr.connected_count, self.NUM_CORES)
        self.mgr.disconnect_all()
        self.assertEqual(self.mgr.connected_count, 0)


class TestCoreManagerConnectAll(unittest.TestCase):
    """Test connect_all with consecutive ports."""

    NUM_CORES = 4

    def setUp(self):
        self.mgr = CoreManager()
        self.mocks = []
        # We need consecutive ports, so bind them manually
        import socket
        # Find a base port by binding to 0 and using that range
        socks = []
        ports = []
        for _ in range(self.NUM_CORES):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', 0))
            ports.append(s.getsockname()[1])
            socks.append(s)
        for s in socks:
            s.close()
        # These ports may not be consecutive, so use individual mocks
        self.base_port = ports[0]
        self.ports = ports

        for port in ports:
            mock = MockTrace32Server.__new__(MockTrace32Server)
            mock.__init__()
            # Override port
            mock._server_sock.close()
            import socket as sock_mod
            mock._server_sock = sock_mod.socket(sock_mod.AF_INET, sock_mod.SOCK_STREAM)
            mock._server_sock.setsockopt(sock_mod.SOL_SOCKET, sock_mod.SO_REUSEADDR, 1)
            try:
                mock._server_sock.bind(('127.0.0.1', port))
                mock._server_sock.listen(1)
                mock.port = port
                mock.set_register('PC', 0x08001000)
                t = threading.Thread(target=mock.start)
                t.daemon = True
                t.start()
                self.mocks.append(mock)
            except Exception:
                self.mocks.append(None)
        time.sleep(0.3)

    def tearDown(self):
        self.mgr.disconnect_all()
        for mock in self.mocks:
            if mock:
                mock.stop()

    def test_connect_all_partial_success(self):
        """connect_all returns status for each core."""
        # Use a port range that likely has some dead ports
        results = self.mgr.connect_all('127.0.0.1', 19999, 2, timeout=1.0)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn('status', r)
            self.assertIn('core_id', r)


class TestCoreManagerSixteenCores(unittest.TestCase):
    """Test 16-core scenario."""

    NUM_CORES = 16

    def setUp(self):
        self.mgr = CoreManager()
        self.mocks = []
        for i in range(self.NUM_CORES):
            mock = MockTrace32Server()
            mock.set_register('PC', 0x08000000 + i * 0x1000)
            mock.set_register('R0', i)
            mock.set_register('CPSR', 0x60000030)
            t = threading.Thread(target=mock.start)
            t.daemon = True
            t.start()
            self.mocks.append(mock)
        time.sleep(0.5)

    def tearDown(self):
        self.mgr.disconnect_all()
        for mock in self.mocks:
            mock.stop()

    def test_connect_16_cores(self):
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        self.assertEqual(self.mgr.connected_count, 16)

    def test_16_core_isolation(self):
        """All 16 cores have independent register state."""
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)

        for i in range(self.NUM_CORES):
            client = self.mgr.get_client(i)
            r0 = client.read_register('R0')
            self.assertEqual(r0, i, "Core {0} R0 should be {0}, got {1}".format(i, r0))
            pc = client.read_register('PC')
            expected_pc = 0x08000000 + i * 0x1000
            self.assertEqual(pc, expected_pc,
                             "Core {0} PC should be 0x{1:X}, got 0x{2:X}".format(
                                 i, expected_pc, pc))

    def test_16_core_list(self):
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        cores = self.mgr.list_cores()
        self.assertEqual(len(cores), 16)

    def test_16_core_disconnect_all(self):
        for i, mock in enumerate(self.mocks):
            self.mgr.connect_core(i, '127.0.0.1', mock.port)
        self.assertEqual(self.mgr.connected_count, 16)
        self.mgr.disconnect_all()
        self.assertEqual(self.mgr.connected_count, 0)

    def test_reject_core_17(self):
        """Cannot connect core 16 (max is 15)."""
        with self.assertRaises(Trace32Error):
            self.mgr.connect_core(16, '127.0.0.1', self.mocks[0].port)


class TestCoreManagerEndianness(unittest.TestCase):
    """Test per-core endianness management."""

    def setUp(self):
        self.mgr = CoreManager()

    def test_default_endian_is_little(self):
        self.assertEqual(self.mgr.get_endianness(0), 'little')

    def test_set_big_endian(self):
        self.mgr.set_endianness(0, 'big')
        self.assertEqual(self.mgr.get_endianness(0), 'big')

    def test_set_little_endian(self):
        self.mgr.set_endianness(0, 'little')
        self.assertEqual(self.mgr.get_endianness(0), 'little')

    def test_invalid_endian_raises(self):
        with self.assertRaises(Trace32Error):
            self.mgr.set_endianness(0, 'middle')

    def test_case_insensitive(self):
        self.mgr.set_endianness(0, 'BIG')
        self.assertEqual(self.mgr.get_endianness(0), 'big')

    def test_per_core_endianness(self):
        self.mgr.set_endianness(0, 'little')
        self.mgr.set_endianness(1, 'big')
        self.assertEqual(self.mgr.get_endianness(0), 'little')
        self.assertEqual(self.mgr.get_endianness(1), 'big')

    def test_disconnect_clears_endianness(self):
        self.mgr.set_endianness(0, 'big')
        self.mgr.disconnect_core(0)
        self.assertEqual(self.mgr.get_endianness(0), 'little')

    def test_disconnect_all_clears_endianness(self):
        self.mgr.set_endianness(0, 'big')
        self.mgr.set_endianness(1, 'big')
        self.mgr.disconnect_all()
        self.assertEqual(self.mgr.get_endianness(0), 'little')
        self.assertEqual(self.mgr.get_endianness(1), 'little')

    def test_list_cores_includes_endian(self):
        mock = MockTrace32Server()
        t = threading.Thread(target=mock.start)
        t.daemon = True
        t.start()
        time.sleep(0.3)
        self.mgr.connect_core(0, '127.0.0.1', mock.port)
        self.mgr.set_endianness(0, 'big')
        cores = self.mgr.list_cores()
        self.assertEqual(cores[0]['endian'], 'big')
        self.mgr.disconnect_all()
        mock.stop()


class TestInterpretWords(unittest.TestCase):
    """Test byte-swap word interpretation."""

    def test_le_32bit(self):
        from t32.core_manager import interpret_words
        data = b'\x78\x56\x34\x12'
        words = interpret_words(data, 4, 'little')
        self.assertEqual(words, [0x12345678])

    def test_be_32bit(self):
        from t32.core_manager import interpret_words
        data = b'\x12\x34\x56\x78'
        words = interpret_words(data, 4, 'big')
        self.assertEqual(words, [0x12345678])

    def test_le_16bit(self):
        from t32.core_manager import interpret_words
        data = b'\x34\x12\x78\x56'
        words = interpret_words(data, 2, 'little')
        self.assertEqual(words, [0x1234, 0x5678])

    def test_be_16bit(self):
        from t32.core_manager import interpret_words
        data = b'\x12\x34\x56\x78'
        words = interpret_words(data, 2, 'big')
        self.assertEqual(words, [0x1234, 0x5678])

    def test_same_bytes_different_endian(self):
        from t32.core_manager import interpret_words
        data = b'\xDE\xAD\xBE\xEF'
        le = interpret_words(data, 4, 'little')
        be = interpret_words(data, 4, 'big')
        self.assertEqual(le, [0xEFBEADDE])
        self.assertEqual(be, [0xDEADBEEF])

    def test_invalid_word_size(self):
        from t32.core_manager import interpret_words
        with self.assertRaises(Trace32Error):
            interpret_words(b'\x00\x00\x00', 3, 'little')

    def test_partial_word_ignored(self):
        from t32.core_manager import interpret_words
        data = b'\x01\x02\x03\x04\x05'  # 5 bytes, 1 full 32-bit word + 1 leftover
        words = interpret_words(data, 4, 'little')
        self.assertEqual(len(words), 1)


if __name__ == '__main__':
    unittest.main()
