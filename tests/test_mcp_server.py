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

import tempfile

import mcp_server
from mcp_server import (
    _handle_request, _make_response, _make_error,
    TOOLS, _HANDLERS, PROMPTS, RESOURCES, RESOURCE_TEMPLATES,
    _PROMPT_CONTENTS, _RESOURCE_CONTENTS, _ANNOTATIONS,
    _send_log, _send_progress, LOG_LEVELS, _PROGRESS_HANDLERS,
    _resolve_resource_template, _handle_completion,
    _cancel_request, _is_cancelled, _clear_cancelled,
    _format_hex_dump, _parse_hex_dump, _resolve_address,
    _cancelled_requests,
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

    def test_resources_list_includes_templates(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}
        response = _handle_request(request)
        result = response["result"]
        self.assertIn("resourceTemplates", result)
        templates = result["resourceTemplates"]
        self.assertIsInstance(templates, list)
        self.assertGreater(len(templates), 0)

    def test_resource_templates_have_required_fields(self):
        for tpl in RESOURCE_TEMPLATES:
            self.assertIn("uriTemplate", tpl)
            self.assertIn("name", tpl)
            self.assertIn("mimeType", tpl)

    def test_read_core_status_disconnected(self):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "resources/read",
            "params": {"uri": "trace32://core/0/status"}
        }
        response = _handle_request(request)
        result = response["result"]
        self.assertIn("contents", result)
        content = result["contents"][0]
        self.assertEqual(content["uri"], "trace32://core/0/status")
        self.assertEqual(content["mimeType"], "application/json")
        data = json.loads(content["text"])
        self.assertEqual(data["core_id"], 0)

    def test_read_core_status_invalid_core_id(self):
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "resources/read",
            "params": {"uri": "trace32://core/99/status"}
        }
        response = _handle_request(request)
        self.assertIn("error", response)

    def test_resolve_template_returns_none_for_unknown(self):
        self.assertIsNone(_resolve_resource_template("trace32://unknown"))

    def test_resolve_template_core_status(self):
        result = _resolve_resource_template("trace32://core/5/status")
        self.assertIsNotNone(result)
        self.assertEqual(result["mimeType"], "application/json")
        data = json.loads(result["text"])
        self.assertIn("core_id", data)


class TestLogging(unittest.TestCase):
    """Test MCP logging (notifications/message)."""

    def setUp(self):
        self.logs = []
        self._orig_sink = mcp_server._notification_sink
        mcp_server._notification_sink = self.logs.append

    def tearDown(self):
        mcp_server._notification_sink = self._orig_sink

    def test_send_log_emits_notification(self):
        _send_log("info", "test message")
        self.assertEqual(len(self.logs), 1)
        msg = self.logs[0]
        self.assertEqual(msg["jsonrpc"], "2.0")
        self.assertEqual(msg["method"], "notifications/message")
        self.assertNotIn("id", msg)
        self.assertEqual(msg["params"]["level"], "info")
        self.assertEqual(msg["params"]["message"], "test message")

    def test_send_log_with_logger_and_data(self):
        _send_log("debug", "test", logger="mylogger", data={"key": "val"})
        params = self.logs[0]["params"]
        self.assertEqual(params["logger"], "mylogger")
        self.assertEqual(params["data"]["key"], "val")

    def test_send_log_invalid_level_defaults_to_info(self):
        _send_log("invalid_level", "test")
        self.assertEqual(self.logs[0]["params"]["level"], "info")

    def test_send_log_no_sink_does_not_crash(self):
        mcp_server._notification_sink = None
        _send_log("error", "should not crash")
        # No exception raised

    def test_tool_call_emits_debug_logs(self):
        request = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "t32_disconnect", "arguments": {}}
        }
        _handle_request(request)
        log_messages = [l["params"]["message"] for l in self.logs]
        self.assertTrue(any("t32_disconnect" in m for m in log_messages))

    def test_tool_call_error_emits_error_log(self):
        request = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "t32_cmd", "arguments": {"command": "test"}}
        }
        _handle_request(request)
        error_logs = [l for l in self.logs if l["params"]["level"] == "error"]
        self.assertGreater(len(error_logs), 0)

    def test_unknown_tool_emits_warning(self):
        request = {
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}}
        }
        _handle_request(request)
        warnings = [l for l in self.logs if l["params"]["level"] == "warning"]
        self.assertGreater(len(warnings), 0)

    def test_initialize_has_logging_capability(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        response = _handle_request(request)
        self.assertIn("logging", response["result"]["capabilities"])

    def test_all_log_levels_valid(self):
        for level in LOG_LEVELS:
            self.logs.clear()
            _send_log(level, "test")
            self.assertEqual(self.logs[0]["params"]["level"], level)


class TestProgress(unittest.TestCase):
    """Test MCP progress notifications."""

    def setUp(self):
        self.notifications = []
        self._orig_sink = mcp_server._notification_sink
        mcp_server._notification_sink = self.notifications.append

    def tearDown(self):
        mcp_server._notification_sink = self._orig_sink

    def test_send_progress_basic(self):
        _send_progress("tok-1", 3, total=10, message="working")
        self.assertEqual(len(self.notifications), 1)
        msg = self.notifications[0]
        self.assertEqual(msg["jsonrpc"], "2.0")
        self.assertEqual(msg["method"], "notifications/progress")
        self.assertNotIn("id", msg)
        p = msg["params"]
        self.assertEqual(p["progressToken"], "tok-1")
        self.assertEqual(p["progress"], 3)
        self.assertEqual(p["total"], 10)
        self.assertEqual(p["message"], "working")

    def test_send_progress_minimal(self):
        _send_progress("tok-2", 5)
        p = self.notifications[0]["params"]
        self.assertEqual(p["progress"], 5)
        self.assertNotIn("total", p)
        self.assertNotIn("message", p)

    def test_send_progress_none_token_does_nothing(self):
        _send_progress(None, 1, total=10)
        self.assertEqual(len(self.notifications), 0)

    def test_send_progress_no_sink_does_not_crash(self):
        mcp_server._notification_sink = None
        _send_progress("tok-3", 1)
        # No exception

    def test_progress_handlers_set_exists(self):
        self.assertIn("t32_connect_all", _PROGRESS_HANDLERS)

    def _mock_connect_all(self, num_cores, progress_token):
        """Run _handle_connect_all with mocked connect_core to avoid network."""
        from mcp_server import _handle_connect_all, _core_manager
        orig = _core_manager.connect_core
        call_count = [0]

        def fake_connect(core_id, host, port, timeout=None):
            call_count[0] += 1
            raise Exception("mock: no T32")

        _core_manager.connect_core = fake_connect
        try:
            return _handle_connect_all(
                {"host": "127.0.0.1", "base_port": 29990,
                 "num_cores": num_cores},
                progress_token=progress_token
            )
        finally:
            _core_manager.connect_core = orig

    def test_connect_all_sends_progress_with_token(self):
        """connect_all with progressToken should emit progress notifications."""
        self._mock_connect_all(2, "test-tok")
        progress_msgs = [n for n in self.notifications
                         if n.get("method") == "notifications/progress"]
        # Should have initial + per-core progress (0, 1, 2 = 3 messages)
        self.assertGreaterEqual(len(progress_msgs), 3)
        tokens = set(n["params"]["progressToken"] for n in progress_msgs)
        self.assertEqual(tokens, {"test-tok"})
        # Last progress should show completion
        last = progress_msgs[-1]["params"]
        self.assertEqual(last["progress"], 2)
        self.assertEqual(last["total"], 2)

    def test_connect_all_no_token_no_progress(self):
        """connect_all without progressToken should not emit progress."""
        self._mock_connect_all(1, None)
        progress_msgs = [n for n in self.notifications
                         if n.get("method") == "notifications/progress"]
        self.assertEqual(len(progress_msgs), 0)

    def test_meta_progress_token_extracted(self):
        """_meta.progressToken in arguments should be passed to handler."""
        self._mock_connect_all(1, "meta-tok")
        progress_msgs = [n for n in self.notifications
                         if n.get("method") == "notifications/progress"]
        self.assertGreater(len(progress_msgs), 0)
        self.assertEqual(progress_msgs[0]["params"]["progressToken"], "meta-tok")


class TestCancellation(unittest.TestCase):
    """Test MCP request cancellation."""

    def setUp(self):
        self.notifications = []
        self._orig_sink = mcp_server._notification_sink
        mcp_server._notification_sink = self.notifications.append
        _cancelled_requests.clear()

    def tearDown(self):
        mcp_server._notification_sink = self._orig_sink
        _cancelled_requests.clear()

    def test_cancel_and_check(self):
        self.assertFalse(_is_cancelled("req-1"))
        _cancel_request("req-1")
        self.assertTrue(_is_cancelled("req-1"))

    def test_clear_cancelled(self):
        _cancel_request("req-2")
        self.assertTrue(_is_cancelled("req-2"))
        _clear_cancelled("req-2")
        self.assertFalse(_is_cancelled("req-2"))

    def test_cancelled_notification_marks_request(self):
        request = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "req-42"}
        }
        _handle_request(request)
        self.assertTrue(_is_cancelled("req-42"))

    def test_cancelled_notification_returns_none(self):
        request = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "req-99"}
        }
        result = _handle_request(request)
        self.assertIsNone(result)

    def test_connect_all_respects_cancellation(self):
        """connect_all should stop when request is cancelled."""
        from mcp_server import _handle_connect_all, _core_manager
        orig = _core_manager.connect_core
        call_count = [0]

        def fake_connect(core_id, host, port, timeout=None):
            call_count[0] += 1
            # Cancel after first core
            if call_count[0] == 1:
                _cancel_request("cancel-test")
            raise Exception("mock")

        _core_manager.connect_core = fake_connect
        try:
            result = _handle_connect_all(
                {"host": "127.0.0.1", "base_port": 29990, "num_cores": 4},
                request_id="cancel-test"
            )
            self.assertTrue(result.get("cancelled", False))
            # Should have stopped after 1st core (cancelled before 2nd)
            self.assertLess(len(result["cores"]), 4)
        finally:
            _core_manager.connect_core = orig

    def test_cancellation_emits_log(self):
        request = {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": "log-test"}
        }
        _handle_request(request)
        log_msgs = [n for n in self.notifications
                    if n.get("method") == "notifications/message"]
        self.assertTrue(any("cancelled" in l["params"]["message"].lower()
                            for l in log_msgs))


class TestCompletion(unittest.TestCase):
    """Test MCP completion/complete."""

    def test_complete_prompt_names(self):
        request = {
            "jsonrpc": "2.0", "id": 1,
            "method": "completion/complete",
            "params": {
                "ref": {"type": "ref/prompt", "name": "trace32-debug-workflow"},
                "argument": {"name": "name", "value": "trace32-d"}
            }
        }
        response = _handle_request(request)
        completion = response["result"]["completion"]
        self.assertIn("trace32-debug-workflow", completion["values"])
        self.assertNotIn("trace32-multicore-workflow", completion["values"])

    def test_complete_prompt_names_all(self):
        result = _handle_completion(
            {"type": "ref/prompt"}, {"name": "name", "value": "trace32"})
        self.assertEqual(len(result["values"]), 2)

    def test_complete_resource_core_ids(self):
        result = _handle_completion(
            {"type": "ref/resource",
             "uri": "trace32://core/{core_id}/status"},
            {"name": "core_id", "value": "1"})
        self.assertIn("1", result["values"])
        self.assertIn("10", result["values"])
        self.assertNotIn("2", result["values"])

    def test_complete_resource_core_ids_empty_prefix(self):
        result = _handle_completion(
            {"type": "ref/resource",
             "uri": "trace32://core/{core_id}/status"},
            {"name": "core_id", "value": ""})
        self.assertEqual(len(result["values"]), 16)

    def test_complete_static_resource_uris(self):
        result = _handle_completion(
            {"type": "ref/resource", "uri": ""},
            {"name": "uri", "value": "trace32://inst"})
        self.assertIn("trace32://instructions", result["values"])

    def test_complete_unknown_ref_type(self):
        result = _handle_completion(
            {"type": "ref/unknown"}, {"name": "x", "value": ""})
        self.assertEqual(result["values"], [])
        self.assertFalse(result["hasMore"])

    def test_completion_via_handle_request(self):
        request = {
            "jsonrpc": "2.0", "id": 1,
            "method": "completion/complete",
            "params": {
                "ref": {"type": "ref/prompt"},
                "argument": {"name": "name", "value": ""}
            }
        }
        response = _handle_request(request)
        self.assertIn("completion", response["result"])
        self.assertIsInstance(response["result"]["completion"]["values"], list)


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


class TestMemoryDumpFormat(unittest.TestCase):
    """Test memory dump/load format functions."""

    def test_format_hex_dump_basic(self):
        data = b'\xDE\xAD\xBE\xEF'
        result = _format_hex_dump(data, 0x1000, "D")
        self.assertIn("D:0x00001000:", result)
        self.assertIn("DE AD BE EF", result)

    def test_format_hex_dump_ascii(self):
        data = b'Hello\x00World!'
        result = _format_hex_dump(data, 0x2000, "D")
        # Printable chars shown, null as dot
        self.assertIn("|Hello.World!|", result)

    def test_format_hex_dump_16_bytes_per_line(self):
        data = bytes(range(32))
        result = _format_hex_dump(data, 0x0, "D")
        lines = [l for l in result.strip().splitlines() if l]
        self.assertEqual(len(lines), 2)
        self.assertIn("D:0x00000000:", lines[0])
        self.assertIn("D:0x00000010:", lines[1])

    def test_format_hex_dump_partial_last_line(self):
        data = bytes(range(20))  # 16 + 4
        result = _format_hex_dump(data, 0x100, "P")
        lines = [l for l in result.strip().splitlines() if l]
        self.assertEqual(len(lines), 2)
        self.assertIn("P:0x00000100:", lines[0])
        self.assertIn("P:0x00000110:", lines[1])

    def test_format_hex_dump_access_prefix(self):
        data = b'\x01'
        result = _format_hex_dump(data, 0x0, "SD")
        self.assertTrue(result.startswith("SD:"))

    def test_parse_hex_dump_roundtrip(self):
        original = b'\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE'
        text = _format_hex_dump(original, 0x1000, "D")
        addr, parsed = _parse_hex_dump(text)
        self.assertEqual(addr, 0x1000)
        self.assertEqual(parsed, original)

    def test_parse_hex_dump_multiline_roundtrip(self):
        original = bytes(range(48))  # 3 lines of 16
        text = _format_hex_dump(original, 0x8000, "D")
        addr, parsed = _parse_hex_dump(text)
        self.assertEqual(addr, 0x8000)
        self.assertEqual(parsed, original)

    def test_parse_hex_dump_empty(self):
        addr, data = _parse_hex_dump("")
        self.assertEqual(addr, 0)
        self.assertEqual(data, b"")

    def test_parse_hex_dump_with_access_prefix(self):
        text = "P:0x00002000: 01 02 03 04  |....|"
        addr, data = _parse_hex_dump(text)
        self.assertEqual(addr, 0x2000)
        self.assertEqual(data, b'\x01\x02\x03\x04')

    def test_resolve_address_integer(self):
        addr, access = _resolve_address(0x1000)
        self.assertEqual(addr, 0x1000)
        self.assertIsNone(access)

    def test_resolve_address_hex_string(self):
        addr, access = _resolve_address("0x2000")
        self.assertEqual(addr, 0x2000)
        self.assertIsNone(access)

    def test_resolve_address_with_access(self):
        addr, access = _resolve_address("D:0x3000")
        self.assertEqual(addr, 0x3000)
        self.assertEqual(access, "D")

    def test_resolve_address_program_access(self):
        addr, access = _resolve_address("P:0x4000")
        self.assertEqual(addr, 0x4000)
        self.assertEqual(access, "P")

    def test_dump_and_load_binary_file(self):
        """Test dump/load with actual file I/O using binary format."""
        original = b'\xDE\xAD\xBE\xEF' * 4
        with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f:
            tmppath = f.name

        try:
            # Write binary
            with open(tmppath, 'wb') as f:
                f.write(original)
            # Read back
            with open(tmppath, 'rb') as f:
                loaded = f.read()
            self.assertEqual(original, loaded)
        finally:
            os.remove(tmppath)

    def test_dump_and_load_text_file(self):
        """Test dump/load with actual file I/O using text format."""
        original = bytes(range(32))
        text = _format_hex_dump(original, 0x1000, "D")

        with tempfile.NamedTemporaryFile(suffix='.txt', mode='w',
                                         delete=False) as f:
            tmppath = f.name
            f.write(text)

        try:
            with open(tmppath, 'r') as f:
                loaded_text = f.read()
            addr, data = _parse_hex_dump(loaded_text)
            self.assertEqual(addr, 0x1000)
            self.assertEqual(data, original)
        finally:
            os.remove(tmppath)

    def test_tools_include_memory_dump_load(self):
        tool_names = [t["name"] for t in TOOLS]
        self.assertIn("t32_memory_dump", tool_names)
        self.assertIn("t32_memory_load", tool_names)

    def test_handlers_include_memory_dump_load(self):
        self.assertIn("t32_memory_dump", _HANDLERS)
        self.assertIn("t32_memory_load", _HANDLERS)

    def test_annotations_include_memory_dump_load(self):
        self.assertIn("t32_memory_dump", _ANNOTATIONS)
        self.assertIn("t32_memory_load", _ANNOTATIONS)
        self.assertFalse(_ANNOTATIONS["t32_memory_dump"]["destructiveHint"])
        self.assertTrue(_ANNOTATIONS["t32_memory_load"]["destructiveHint"])


if __name__ == '__main__':
    unittest.main()
