#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Multi-core manager for TRACE32 connections.

Manages multiple Trace32Client instances, one per core.
Supports up to MAX_CORES (16) simultaneous connections.

Compatible with Python 2.7 and 3.4+.
"""
from __future__ import print_function

from .client import Trace32Client, Trace32Error
from .constants import MAX_CORES, DEFAULT_TIMEOUT


class CoreManager(object):
    """Manages multiple Trace32Client instances for multi-core debugging.

    Each core is identified by an integer core_id (0 to MAX_CORES-1).
    Backward compatible: core_id defaults to 0 everywhere.
    """

    def __init__(self, max_cores=MAX_CORES):
        self._clients = {}  # core_id -> Trace32Client
        self._max_cores = max_cores

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
        for core_id in list(self._clients.keys()):
            try:
                self._clients[core_id].disconnect()
            except Exception:
                pass
        self._clients.clear()

    def list_cores(self):
        """Return status of all connected cores.

        Returns:
            List of dicts with core_id, connected, host, port.
        """
        result = []
        for core_id in sorted(self._clients.keys()):
            client = self._clients[core_id]
            result.append({
                "core_id": core_id,
                "connected": client.connected,
                "host": client._host,
                "port": client._port,
            })
        return result

    @property
    def connected_count(self):
        """Number of currently connected cores."""
        return sum(1 for c in self._clients.values() if c.connected)
