#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unit tests for mcp_server module.

Tests the MCP JSON-RPC protocol handling and tool dispatch
without requiring actual TRACE32 hardware.
"""
from __future__ import print_function

import json
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp_server import (
    _handle_request, _make_response, _make_error,
    TOOLS, _HANDLERS,
)


class TestJsonRpcHelpers(unittest.TestCase):
    """Test JSON-RPC message building."""

    def test_make_response(self):
        resp = _make_response(1, {"key": "value"})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["key"], "value")

    def test_make_error(self):
        resp = _make_error(2, -32601, "Method not found")
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 2)
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertEqual(resp["error"]["message"], "Method not found")


class TestInitialize(unittest.TestCase):
    """Test MCP initialize handshake."""

    def test_initialize_returns_capabilities(self):
        request = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0.0"}
            }
        }
        response = _handle_request(request)
        self.assertIsNotNone(response)
        self.assertEqual(response["id"], 0)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        self.assertIn("serverInfo", result)
        self.assertEqual(result["serverInfo"]["name"], "trace32-mcp-server")

    def test_initialize_result_is_valid_json(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        response = _handle_request(request)
        # Should be serializable
        serialized = json.dumps(response)
        self.assertIsInstance(serialized, str)


class TestNotifications(unittest.TestCase):
    """Notifications (no id) should return None."""

    def test_initialized_notification(self):
        request = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        response = _handle_request(request)
        self.assertIsNone(response)

    def test_cancelled_notification(self):
        request = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 5}}
        response = _handle_request(request)
        self.assertIsNone(response)


class TestToolsList(unittest.TestCase):
    """Test tools/list method."""

    def test_returns_tools(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = _handle_request(request)
        self.assertIsNotNone(response)
        tools = response["result"]["tools"]
        self.assertIsInstance(tools, list)
        self.assertGreater(len(tools), 0)

    def test_all_tools_have_name(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = _handle_request(request)
        tools = response["result"]["tools"]
        for tool in tools:
            self.assertIn("name", tool)
            self.assertIn("description", tool)

    def test_tool_names_start_with_t32(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = _handle_request(request)
        tools = response["result"]["tools"]
        for tool in tools:
            self.assertTrue(tool["name"].startswith("t32_"),
                            "Tool {0} doesn't start with 't32_'".format(tool["name"]))

    def test_all_tools_have_handlers(self):
        """Every listed tool must have a handler in _HANDLERS."""
        for tool in TOOLS:
            self.assertIn(tool["name"], _HANDLERS,
                          "Tool '{0}' has no handler".format(tool["name"]))


class TestToolsCall(unittest.TestCase):
    """Test tools/call method routing."""

    def test_unknown_tool_returns_error(self):
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {}
            }
        }
        response = _handle_request(request)
        self.assertIn("isError", response["result"])
        self.assertTrue(response["result"]["isError"])

    def test_tool_call_without_connection_returns_error(self):
        """Calling a tool that needs connection should return an error, not crash."""
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "t32_cmd",
                "arguments": {"command": "SYStem.Up"}
            }
        }
        response = _handle_request(request)
        self.assertIsNotNone(response)
        self.assertEqual(response["id"], 3)
        # Should have isError since we're not connected
        self.assertTrue(response["result"].get("isError", False))
        content = response["result"]["content"]
        self.assertIsInstance(content, list)
        self.assertGreater(len(content), 0)

    def test_disconnect_without_connection(self):
        """Disconnect when not connected should succeed."""
        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "t32_disconnect",
                "arguments": {}
            }
        }
        response = _handle_request(request)
        self.assertIsNotNone(response)
        # Disconnect should not error even when not connected
        content_text = response["result"]["content"][0]["text"]
        data = json.loads(content_text)
        self.assertEqual(data["status"], "disconnected")


class TestMethodNotFound(unittest.TestCase):
    def test_unknown_method(self):
        request = {"jsonrpc": "2.0", "id": 5, "method": "unknown/method", "params": {}}
        response = _handle_request(request)
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32601)

    def test_resources_list_returns_empty(self):
        request = {"jsonrpc": "2.0", "id": 6, "method": "resources/list", "params": {}}
        response = _handle_request(request)
        self.assertEqual(response["result"]["resources"], [])

    def test_prompts_list_returns_empty(self):
        request = {"jsonrpc": "2.0", "id": 7, "method": "prompts/list", "params": {}}
        response = _handle_request(request)
        self.assertEqual(response["result"]["prompts"], [])

    def test_ping(self):
        request = {"jsonrpc": "2.0", "id": 8, "method": "ping", "params": {}}
        response = _handle_request(request)
        self.assertEqual(response["result"], {})


class TestToolSchemas(unittest.TestCase):
    """Validate tool schema structures."""

    def test_tools_with_required_params_have_input_schema(self):
        """Tools that need arguments should have inputSchema."""
        needs_args = ['t32_cmd', 't32_eval', 't32_read_memory', 't32_write_memory',
                      't32_read_register', 't32_write_register', 't32_breakpoint_set',
                      't32_read_variable', 't32_write_variable', 't32_get_symbol',
                      't32_run_script', 't32_load']
        tool_map = {t["name"]: t for t in TOOLS}
        for name in needs_args:
            self.assertIn("inputSchema", tool_map[name],
                          "Tool '{0}' needs inputSchema".format(name))

    def test_input_schema_is_object_type(self):
        for tool in TOOLS:
            schema = tool.get("inputSchema")
            if schema:
                self.assertEqual(schema["type"], "object")

    def test_required_fields_are_in_properties(self):
        for tool in TOOLS:
            schema = tool.get("inputSchema")
            if schema and "required" in schema:
                props = schema.get("properties", {})
                for req in schema["required"]:
                    self.assertIn(req, props,
                                  "Tool '{0}': required field '{1}' not in properties".format(
                                      tool["name"], req))


class TestMcpProtocolCompliance(unittest.TestCase):
    """Verify MCP protocol compliance."""

    def test_response_has_jsonrpc_field(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        response = _handle_request(request)
        self.assertEqual(response["jsonrpc"], "2.0")

    def test_response_preserves_id(self):
        for req_id in [0, 1, 42, "string-id", None]:
            request = {"jsonrpc": "2.0", "id": req_id, "method": "ping", "params": {}}
            response = _handle_request(request)
            if req_id is not None:
                self.assertEqual(response["id"], req_id)
            else:
                self.assertIsNone(response)

    def test_tool_call_result_has_content_array(self):
        """MCP tools/call result must have content array."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "t32_disconnect", "arguments": {}}
        }
        response = _handle_request(request)
        result = response["result"]
        self.assertIn("content", result)
        self.assertIsInstance(result["content"], list)
        self.assertGreater(len(result["content"]), 0)
        self.assertEqual(result["content"][0]["type"], "text")


if __name__ == '__main__':
    unittest.main()
