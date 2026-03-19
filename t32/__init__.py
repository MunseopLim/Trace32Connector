#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TRACE32 Remote API Client Library.

Pure Python implementation of the Lauterbach TRACE32 Remote Control
protocol over TCP (NETTCP). Compatible with Python 2.7 and 3.4+.
No external dependencies required.
"""
from __future__ import print_function

from .client import Trace32Client, Trace32Error
from .constants import *

__version__ = '1.0.0'
