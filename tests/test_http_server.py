#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for http_server module.

Tests HTTP request routing and response formatting.
"""
from __future__ import print_function

import json
import unittest
import sys
import os

try:
    from http.server import HTTPServer
except ImportError:
    from BaseHTTPServer import HTTPServer

try:
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError, URLError
except ImportError:
    from urllib2 import urlopen, Request, HTTPError, URLError

import threading
import time
import socket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from http_server import Trace32Handler, _POST_ROUTES, _GET_ROUTES


def _find_free_port():
    """Find an available TCP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestRouteDefinitions(unittest.TestCase):
    """Verify route table completeness."""

    def test_post_routes_not_empty(self):
        self.assertGreater(len(_POST_ROUTES), 0)

    def test_get_routes_not_empty(self):
        self.assertGreater(len(_GET_ROUTES), 0)

    def test_all_routes_start_with_api(self):
        for path in list(_POST_ROUTES.keys()) + list(_GET_ROUTES.keys()):
            self.assertTrue(path.startswith('/api/'),
                            "Route '{0}' doesn't start with '/api/'".format(path))

    def test_all_routes_are_callable(self):
        for path, handler in list(_POST_ROUTES.items()) + list(_GET_ROUTES.items()):
            self.assertTrue(callable(handler),
                            "Handler for '{0}' is not callable".format(path))

    def test_connect_route_exists(self):
        self.assertIn('/api/connect', _POST_ROUTES)

    def test_disconnect_route_exists(self):
        self.assertIn('/api/disconnect', _POST_ROUTES)

    def test_cmd_route_exists(self):
        self.assertIn('/api/cmd', _POST_ROUTES)

    def test_state_route_exists(self):
        self.assertIn('/api/state', _GET_ROUTES)

    def test_memory_routes_exist(self):
        self.assertIn('/api/memory/read', _POST_ROUTES)
        self.assertIn('/api/memory/write', _POST_ROUTES)

    def test_register_routes_exist(self):
        self.assertIn('/api/register/read', _POST_ROUTES)
        self.assertIn('/api/register/write', _POST_ROUTES)

    def test_breakpoint_routes_exist(self):
        self.assertIn('/api/breakpoint/set', _POST_ROUTES)
        self.assertIn('/api/breakpoint/delete', _POST_ROUTES)
        self.assertIn('/api/breakpoint/list', _GET_ROUTES)


class TestHttpServer(unittest.TestCase):
    """Test actual HTTP server responses (without TRACE32 connection)."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = HTTPServer(('127.0.0.1', cls.port), Trace32Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()
        time.sleep(0.1)
        cls.base_url = 'http://127.0.0.1:{0}'.format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        """Make a GET request."""
        url = self.base_url + path
        req = Request(url)
        try:
            resp = urlopen(req, timeout=5)
            body = resp.read()
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            return resp.getcode(), json.loads(body)
        except HTTPError as e:
            body = e.read()
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            return e.code, json.loads(body)

    def _post(self, path, data=None):
        """Make a POST request with JSON body."""
        url = self.base_url + path
        body = json.dumps(data or {})
        if sys.version_info[0] >= 3:
            body = body.encode('utf-8')
        req = Request(url, data=body)
        req.add_header('Content-Type', 'application/json')
        try:
            resp = urlopen(req, timeout=5)
            resp_body = resp.read()
            if isinstance(resp_body, bytes):
                resp_body = resp_body.decode('utf-8')
            return resp.getcode(), json.loads(resp_body)
        except HTTPError as e:
            resp_body = e.read()
            if isinstance(resp_body, bytes):
                resp_body = resp_body.decode('utf-8')
            return e.code, json.loads(resp_body)

    def test_get_api_tools(self):
        """GET /api/tools returns route list."""
        code, data = self._get('/api/tools')
        self.assertEqual(code, 200)
        self.assertIn('tools', data)
        self.assertIsInstance(data['tools'], list)
        self.assertGreater(len(data['tools']), 0)

    def test_get_unknown_route_404(self):
        """Unknown GET route returns 404."""
        code, data = self._get('/api/nonexistent')
        self.assertEqual(code, 404)
        self.assertIn('error', data)

    def test_post_unknown_route_404(self):
        """Unknown POST route returns 404."""
        code, data = self._post('/api/nonexistent', {})
        self.assertEqual(code, 404)
        self.assertIn('error', data)

    def test_post_disconnect(self):
        """POST /api/disconnect should work even without connection."""
        code, data = self._post('/api/disconnect', {})
        self.assertEqual(code, 200)
        self.assertEqual(data['status'], 'disconnected')

    def test_post_cmd_without_connection(self):
        """POST /api/cmd without connection returns error."""
        code, data = self._post('/api/cmd', {"command": "SYStem.Up"})
        self.assertEqual(code, 500)
        self.assertIn('error', data)

    def test_post_cmd_missing_field(self):
        """POST /api/cmd without command field returns 400."""
        code, data = self._post('/api/cmd', {})
        self.assertEqual(code, 400)
        self.assertIn('error', data)

    def test_get_state_without_connection(self):
        """GET /api/state without connection returns error."""
        code, data = self._get('/api/state')
        self.assertEqual(code, 500)
        self.assertIn('error', data)

    def test_post_connect_invalid_host(self):
        """POST /api/connect with unreachable host returns error."""
        code, data = self._post('/api/connect', {
            "host": "127.0.0.1",
            "port": 1
        })
        self.assertEqual(code, 500)
        self.assertIn('error', data)

    def test_response_has_cors_headers(self):
        """Responses should include CORS headers."""
        url = self.base_url + '/api/tools'
        req = Request(url)
        resp = urlopen(req, timeout=5)
        # Check for Access-Control-Allow-Origin
        cors = resp.headers.get('Access-Control-Allow-Origin') if hasattr(resp.headers, 'get') \
            else resp.headers.getheader('Access-Control-Allow-Origin')
        self.assertEqual(cors, '*')

    def test_response_is_valid_json(self):
        """All responses are valid JSON."""
        code, data = self._get('/api/tools')
        self.assertIsInstance(data, dict)
        serialized = json.dumps(data)
        re_parsed = json.loads(serialized)
        self.assertEqual(data, re_parsed)


if __name__ == '__main__':
    unittest.main()
