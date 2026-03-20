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
    TOOLS, _HANDLERS, PROMPTS, RESOURCES,
    _PROMPT_CONTENTS, _RESOURCE_CONTENTS, _ANNOTATIONS,
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
        self.assertIn("prompts", result["capabilities"])
        self.assertIn("resources", result["capabilities"])
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

    def test_resources_list_returns_resources(self):
        request = {"jsonrpc": "2.0", "id": 6, "method": "resources/list", "params": {}}
        response = _handle_request(request)
        resources = response["result"]["resources"]
        self.assertIsInstance(resources, list)
        self.assertGreater(len(resources), 0)

    def test_prompts_list_returns_prompts(self):
        request = {"jsonrpc": "2.0", "id": 7, "method": "prompts/list", "params": {}}
        response = _handle_request(request)
        prompts = response["result"]["prompts"]
        self.assertIsInstance(prompts, list)
        self.assertGreater(len(prompts), 0)

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


class TestToolAnnotations(unittest.TestCase):
    """Test MCP tool annotations."""

    def test_all_tools_have_annotations(self):
        for tool in TOOLS:
            self.assertIn("annotations", tool,
                          "Tool '{0}' missing annotations".format(tool["name"]))

    def test_all_annotations_have_required_fields(self):
        required = ["title", "readOnlyHint", "destructiveHint",
                     "idempotentHint", "openWorldHint"]
        for tool in TOOLS:
            ann = tool.get("annotations", {})
            for field in required:
                self.assertIn(field, ann,
                              "Tool '{0}' annotation missing '{1}'".format(
                                  tool["name"], field))

    def test_annotations_cover_all_tools(self):
        for tool in TOOLS:
            self.assertIn(tool["name"], _ANNOTATIONS)

    def test_read_tools_are_readonly(self):
        read_tools = ["t32_read_memory", "t32_read_register",
                      "t32_read_variable", "t32_eval", "t32_get_state",
                      "t32_get_endian", "t32_get_symbol", "t32_get_version",
                      "t32_list_cores", "t32_breakpoint_list"]
        for name in read_tools:
            ann = _ANNOTATIONS[name]
            self.assertTrue(ann["readOnlyHint"],
                            "'{0}' should be readOnly".format(name))
            self.assertFalse(ann["destructiveHint"],
                             "'{0}' should not be destructive".format(name))

    def test_write_tools_are_destructive(self):
        destructive_tools = ["t32_write_memory", "t32_write_register",
                             "t32_write_variable", "t32_cmd",
                             "t32_breakpoint_delete", "t32_run_script",
                             "t32_load"]
        for name in destructive_tools:
            ann = _ANNOTATIONS[name]
            self.assertTrue(ann["destructiveHint"],
                            "'{0}' should be destructive".format(name))
            self.assertFalse(ann["readOnlyHint"],
                             "'{0}' should not be readOnly".format(name))

    def test_annotations_in_tools_list_response(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = _handle_request(request)
        tools = response["result"]["tools"]
        for tool in tools:
            self.assertIn("annotations", tool)
            self.assertIn("title", tool["annotations"])


class TestPrompts(unittest.TestCase):
    """Test MCP prompts functionality."""

    def test_prompts_list_returns_all(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "prompts/list", "params": {}}
        response = _handle_request(request)
        prompts = response["result"]["prompts"]
        names = [p["name"] for p in prompts]
        self.assertIn("trace32-debug-workflow", names)
        self.assertIn("trace32-multicore-workflow", names)

    def test_prompts_have_description(self):
        for prompt in PROMPTS:
            self.assertIn("name", prompt)
            self.assertIn("description", prompt)
            self.assertTrue(len(prompt["description"]) > 0)

    def test_all_prompts_have_content(self):
        for prompt in PROMPTS:
            self.assertIn(prompt["name"], _PROMPT_CONTENTS,
                          "Prompt '{0}' has no content".format(prompt["name"]))

    def test_prompts_get_debug_workflow(self):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "prompts/get",
            "params": {"name": "trace32-debug-workflow"}
        }
        response = _handle_request(request)
        result = response["result"]
        self.assertIn("messages", result)
        self.assertIsInstance(result["messages"], list)
        self.assertGreater(len(result["messages"]), 0)
        msg = result["messages"][0]
        self.assertEqual(msg["role"], "user")
        self.assertIn("MCP tools", msg["content"]["text"])

    def test_prompts_get_multicore_workflow(self):
        request = {
            "jsonrpc": "2.0", "id": 2, "method": "prompts/get",
            "params": {"name": "trace32-multicore-workflow"}
        }
        response = _handle_request(request)
        result = response["result"]
        self.assertIn("messages", result)
        self.assertIn("multi-core", result["messages"][0]["content"]["text"].lower())

    def test_prompts_get_unknown_returns_error(self):
        request = {
            "jsonrpc": "2.0", "id": 3, "method": "prompts/get",
            "params": {"name": "nonexistent"}
        }
        response = _handle_request(request)
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32602)

    def test_prompt_content_mentions_no_scripts(self):
        """Debug workflow should tell AI not to write scripts."""
        content = _PROMPT_CONTENTS["trace32-debug-workflow"]
        self.assertIn("Do NOT", content)
        self.assertIn("script", content.lower())


class TestResources(unittest.TestCase):
    """Test MCP resources functionality."""

    def test_resources_list_returns_all(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}
        response = _handle_request(request)
        resources = response["result"]["resources"]
        uris = [r["uri"] for r in resources]
        self.assertIn("trace32://instructions", uris)

    def test_resources_have_required_fields(self):
        for resource in RESOURCES:
            self.assertIn("uri", resource)
            self.assertIn("name", resource)
            self.assertIn("mimeType", resource)

    def test_all_resources_have_content(self):
        for resource in RESOURCES:
            self.assertIn(resource["uri"], _RESOURCE_CONTENTS,
                          "Resource '{0}' has no content".format(resource["uri"]))

    def test_resources_read_instructions(self):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "resources/read",
            "params": {"uri": "trace32://instructions"}
        }
        response = _handle_request(request)
        result = response["result"]
        self.assertIn("contents", result)
        self.assertIsInstance(result["contents"], list)
        self.assertGreater(len(result["contents"]), 0)
        content = result["contents"][0]
        self.assertEqual(content["uri"], "trace32://instructions")
        self.assertEqual(content["mimeType"], "text/plain")
        self.assertIn("CRITICAL RULES", content["text"])

    def test_resources_read_unknown_returns_error(self):
        request = {
            "jsonrpc": "2.0", "id": 2, "method": "resources/read",
            "params": {"uri": "trace32://nonexistent"}
        }
        response = _handle_request(request)
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32602)

    def test_instructions_mention_no_scripts(self):
        """Instructions resource should tell AI to use tools directly."""
        content = _RESOURCE_CONTENTS["trace32://instructions"]
        self.assertIn("Do NOT generate Python scripts", content)
        self.assertIn("Use the MCP tools directly", content)


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
