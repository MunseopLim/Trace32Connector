#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for t32.constants module."""
from __future__ import print_function

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from t32.constants import (
    CMD_NOP, CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING,
    CMD_DEVICE_SPECIFIC, CMD_GETMSG, CMD_TERMINATE,
    SUBCMD_GET_STATE, SUBCMD_READ_MEMORY, SUBCMD_WRITE_MEMORY,
    SUBCMD_READ_REG_BY_NAME, SUBCMD_READ_PP,
    DEV_ICD, DEV_OS,
    STATE_DOWN, STATE_HALTED, STATE_STOPPED, STATE_RUNNING, STATE_NAMES,
    ACCESS_CLASSES, ACCESS_DATA, ACCESS_PROGRAM,
    ERR_OK, ERROR_NAMES,
    BP_PROGRAM, BP_READ, BP_WRITE, BP_TYPES,
    DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT,
)


class TestCommandIDs(unittest.TestCase):
    """Verify command IDs are in the expected range (0x70-0x79)."""

    def test_command_ids_range(self):
        cmds = [CMD_NOP, CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING,
                CMD_DEVICE_SPECIFIC, CMD_GETMSG, CMD_TERMINATE]
        for cmd in cmds:
            self.assertGreaterEqual(cmd, 0x70)
            self.assertLessEqual(cmd, 0x79)

    def test_command_ids_unique(self):
        cmds = [CMD_NOP, CMD_ATTACH, CMD_EXECUTE_PRACTICE, CMD_PING,
                CMD_DEVICE_SPECIFIC, CMD_GETMSG, CMD_TERMINATE]
        self.assertEqual(len(cmds), len(set(cmds)))

    def test_specific_values(self):
        self.assertEqual(CMD_NOP, 0x70)
        self.assertEqual(CMD_ATTACH, 0x71)
        self.assertEqual(CMD_EXECUTE_PRACTICE, 0x72)
        self.assertEqual(CMD_PING, 0x73)
        self.assertEqual(CMD_DEVICE_SPECIFIC, 0x74)
        self.assertEqual(CMD_GETMSG, 0x76)
        self.assertEqual(CMD_TERMINATE, 0x79)


class TestSubCommands(unittest.TestCase):
    """Verify device-specific sub-command IDs."""

    def test_subcmd_values(self):
        self.assertEqual(SUBCMD_GET_STATE, 0x10)
        self.assertEqual(SUBCMD_READ_PP, 0x22)
        self.assertEqual(SUBCMD_READ_REG_BY_NAME, 0x23)
        self.assertEqual(SUBCMD_READ_MEMORY, 0x30)
        self.assertEqual(SUBCMD_WRITE_MEMORY, 0x31)


class TestDeviceTypes(unittest.TestCase):
    def test_dev_icd(self):
        self.assertEqual(DEV_ICD, 0x01)

    def test_dev_os(self):
        self.assertEqual(DEV_OS, 0x00)


class TestStateConstants(unittest.TestCase):
    def test_state_values(self):
        self.assertEqual(STATE_DOWN, 0x00)
        self.assertEqual(STATE_HALTED, 0x01)
        self.assertEqual(STATE_STOPPED, 0x02)
        self.assertEqual(STATE_RUNNING, 0x03)

    def test_state_names_complete(self):
        for state in [STATE_DOWN, STATE_HALTED, STATE_STOPPED, STATE_RUNNING]:
            self.assertIn(state, STATE_NAMES)

    def test_state_name_strings(self):
        self.assertEqual(STATE_NAMES[STATE_DOWN], 'down')
        self.assertEqual(STATE_NAMES[STATE_HALTED], 'halted')
        self.assertEqual(STATE_NAMES[STATE_STOPPED], 'stopped')
        self.assertEqual(STATE_NAMES[STATE_RUNNING], 'running')


class TestAccessClasses(unittest.TestCase):
    def test_data_access(self):
        self.assertEqual(ACCESS_CLASSES['D'], ACCESS_DATA)

    def test_program_access(self):
        self.assertEqual(ACCESS_CLASSES['P'], ACCESS_PROGRAM)

    def test_all_keys_uppercase(self):
        for key in ACCESS_CLASSES:
            self.assertEqual(key, key.upper())


class TestErrorCodes(unittest.TestCase):
    def test_ok_is_zero(self):
        self.assertEqual(ERR_OK, 0x00)

    def test_error_names_has_ok(self):
        self.assertIn(ERR_OK, ERROR_NAMES)
        self.assertEqual(ERROR_NAMES[ERR_OK], 'OK')


class TestBreakpointTypes(unittest.TestCase):
    def test_bp_program(self):
        self.assertEqual(BP_TYPES['program'], BP_PROGRAM)

    def test_bp_read(self):
        self.assertEqual(BP_TYPES['read'], BP_READ)

    def test_bp_write(self):
        self.assertEqual(BP_TYPES['write'], BP_WRITE)


class TestDefaults(unittest.TestCase):
    def test_default_host(self):
        self.assertEqual(DEFAULT_HOST, 'localhost')

    def test_default_port(self):
        self.assertEqual(DEFAULT_PORT, 20000)

    def test_default_timeout(self):
        self.assertEqual(DEFAULT_TIMEOUT, 10.0)


if __name__ == '__main__':
    unittest.main()
