#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Multi-core manager for TRACE32 connections.

Manages multiple Trace32Client instances, one per core.
Supports up to MAX_CORES (16) simultaneous connections.
Per-core endianness setting for correct memory interpretation.

Compatible with Python 2.7 and 3.4+.
"""
from __future__ import print_function

import struct
import sys
import threading

from .client import Trace32Client, Trace32Error
from .constants import (
    MAX_CORES, DEFAULT_TIMEOUT,
    ENDIAN_LITTLE, ENDIAN_BIG, ENDIAN_DEFAULT, VALID_ENDIANNESS,
)


def interpret_words(data, word_size, endian):
    """Interpret raw bytes as a list of word values.

    Args:
        data: bytes/bytearray of memory content.
        word_size: 2 or 4 (bytes per word).
        endian: 'little' or 'big'.

    Returns:
        List of integer word values.
    """
    if word_size not in (2, 4):
        raise Trace32Error("word_size must be 2 or 4, got {0}".format(word_size))
    fmt_char = 'H' if word_size == 2 else 'I'
    prefix = '<' if endian == ENDIAN_LITTLE else '>'
    count = len(data) // word_size
    result = []
    for i in range(count):
        offset = i * word_size
        value = struct.unpack_from(prefix + fmt_char, bytes(data), offset)[0]
        result.append(value)
    return result


class CoreManager(object):
    """Manages multiple Trace32Client instances for multi-core debugging.

    Each core is identified by an integer core_id (0 to MAX_CORES-1).
    Backward compatible: core_id defaults to 0 everywhere.
    """

    def __init__(self, max_cores=MAX_CORES, keepalive_interval=30):
        self._clients = {}      # core_id -> Trace32Client
        self._endianness = {}   # core_id -> 'little' | 'big'
        self._max_cores = max_cores
        self._keepalive_interval = keepalive_interval
        self._keepalive_stop = threading.Event()
        self._keepalive_thread = None

    def _validate_core_id(self, core_id):
        """Validate and normalize core_id to int."""
        core_id = int(core_id)
        if core_id < 0 or core_id >= self._max_cores:
            raise Trace32Error(
                "core_id must be 0-{0}, got {1}".format(
                    self._max_cores - 1, core_id))
        return core_id

    def get_client(self, core_id=0):
        """Get the Trace32Client for a given core.

        Args:
            core_id: Core identifier (0-15, default 0).

        Returns:
            Connected Trace32Client instance.

        Raises:
            Trace32Error: If core_id is invalid or core is not connected.
        """
        core_id = self._validate_core_id(core_id)
        client = self._clients.get(core_id)
        if client is None or not client.connected:
            raise Trace32Error(
                "Core {0} is not connected. Call connect first.".format(core_id))
        return client

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect_core(self, core_id, host, port, timeout=DEFAULT_TIMEOUT):
        """Connect a single core.

        Args:
            core_id: Core identifier (0-15).
            host: TRACE32 host address.
            port: TRACE32 RCL port.
            timeout: Connection timeout in seconds.

        Returns:
            Connected Trace32Client instance.
        """
        core_id = self._validate_core_id(core_id)
        if core_id in self._clients and self._clients[core_id].connected:
            self._clients[core_id].disconnect()
        client = Trace32Client()
        client.connect(host=str(host), port=int(port), timeout=timeout)
        self._clients[core_id] = client
        self._start_keepalive()
        return client

    def disconnect_core(self, core_id):
        """Disconnect a single core.

        Args:
            core_id: Core identifier (0-15).
        """
        core_id = self._validate_core_id(core_id)
        client = self._clients.pop(core_id, None)
        if client:
            client.disconnect()
        self._endianness.pop(core_id, None)

    def connect_all(self, host, base_port, num_cores, timeout=DEFAULT_TIMEOUT):
        """Connect to multiple cores on consecutive ports.

        Args:
            host: TRACE32 host address.
            base_port: First port number.
            num_cores: Number of cores to connect.
            timeout: Connection timeout per core.

        Returns:
            List of dicts with core_id, port, status, and optional error.
        """
        if num_cores > self._max_cores:
            raise Trace32Error(
                "num_cores ({0}) exceeds max ({1})".format(
                    num_cores, self._max_cores))
        results = []
        for i in range(num_cores):
            port = base_port + i
            try:
                self.connect_core(i, host, port, timeout)
                results.append({
                    "core_id": i, "port": port, "status": "connected"
                })
            except Trace32Error as e:
                results.append({
                    "core_id": i, "port": port,
                    "status": "failed", "error": str(e)
                })
        return results

    def disconnect_all(self):
        """Disconnect all cores."""
        self._stop_keepalive()
        for core_id in list(self._clients.keys()):
            try:
                self._clients[core_id].disconnect()
            except Exception:
                pass
        self._clients.clear()
        self._endianness.clear()

    # ------------------------------------------------------------------
    # Endianness
    # ------------------------------------------------------------------

    def set_endianness(self, core_id, endian):
        """Set target endianness for a core.

        Args:
            core_id: Core identifier (0-15).
            endian: 'little' or 'big'.
        """
        core_id = self._validate_core_id(core_id)
        endian = str(endian).lower()
        if endian not in VALID_ENDIANNESS:
            raise Trace32Error(
                "endian must be 'little' or 'big', got '{0}'".format(endian))
        self._endianness[core_id] = endian

    def get_endianness(self, core_id=0):
        """Get target endianness for a core.

        Args:
            core_id: Core identifier (0-15).

        Returns:
            'little' or 'big'.
        """
        core_id = self._validate_core_id(core_id)
        return self._endianness.get(core_id, ENDIAN_DEFAULT)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def list_cores(self):
        """Return status of all connected cores.

        Returns:
            List of dicts with core_id, connected, host, port, endian.
        """
        result = []
        for core_id in sorted(self._clients.keys()):
            client = self._clients[core_id]
            result.append({
                "core_id": core_id,
                "connected": client.connected,
                "host": client._host,
                "port": client._port,
                "endian": self._endianness.get(core_id, ENDIAN_DEFAULT),
            })
        return result

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    def _start_keepalive(self):
        """Start keepalive thread if not already running."""
        if self._keepalive_thread is not None:
            return
        self._keepalive_stop.clear()
        t = threading.Thread(target=self._keepalive_loop)
        t.daemon = True
        t.start()
        self._keepalive_thread = t

    def _stop_keepalive(self):
        """Stop keepalive thread."""
        if self._keepalive_thread is None:
            return
        self._keepalive_stop.set()
        self._keepalive_thread.join(timeout=5)
        self._keepalive_thread = None

    def _keepalive_loop(self):
        """Background loop that pings all connected cores."""
        while not self._keepalive_stop.wait(self._keepalive_interval):
            for client in list(self._clients.values()):
                if self._keepalive_stop.is_set():
                    return
                if client.connected:
                    try:
                        client.ping()
                    except Exception:
                        sys.stderr.write(
                            "keepalive: ping failed for {0}:{1}\n".format(
                                client._host, client._port))
                        sys.stderr.flush()

    @property
    def connected_count(self):
        """Number of currently connected cores."""
        return sum(1 for c in self._clients.values() if c.connected)
