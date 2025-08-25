# noinspection SpellCheckingInspection
"""
Script:         mcp_service.py
Author:         DevOps Team

Description:
    Minimal Model Context Protocol (MCP) service layer for AutoForge.

    This adapter exposes AutoForge commands as MCP tools over HTTP/SSE,
    speaking JSON-RPC 2.0. It is intended as the bridge between
    AutoForge’s internal APIs and external MCP-aware clients
    (e.g. VS Code, MCP CLI, automation bots).

    Key design points:
      - Single-workspace, single-flight: only one tool executes at a time,
        concurrent calls are rejected with a JSON-RPC error.
      - Provides clean startup/shutdown hooks and status telemetry.
      - Returns all errors as JSON-RPC envelopes (never HTTP 500).
      - Service does not require authentication; any MCP client
        can connect and execute tools.

    This makes AutoForge usable in distributed or IDE-driven workflows
    without requiring full SDK integration.

    Note:
        Test using: Inspector fom https://modelcontextprotocol.io/legacy/tools/inspector
        Install from https://github.com/modelcontextprotocol/inspector (node.js)
        WSL: Must disable 'networkingmode = mirrored' in C:\\Users\\<YourWindowsUsername>\\.wslconfig
        Example:
            Using in Windows Command Prompt
            set MCP_PROXY_AUTH_TOKEN=foobar
            npx @modelcontextprotocol/inspector http://10.12.148.90:6274
"""

import asyncio
import contextlib
import json
import os
import re
import shlex
import signal
import socket
from dataclasses import dataclass
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Optional, Any, Union
from urllib.parse import urlparse, parse_qsl, unquote

# Third-party
from aiohttp import web

# MCP Service imports
from logger import CoreMCPLogger

AUTO_FORGE_MODULE_NAME = "MCP"
AUTO_FORGE_MODULE_DESCRIPTION = "MCP (Model Context Protocol) integration for AutoForge"
AUTO_FORGE_MAX_BATCH_MCP_COMMANDS = 64
AUTO_FORGE_BUSY_CODE = -32004


@dataclass
class _CoreMCPConfigType:
    """ Configuration for the MCP server connection. """
    host: Optional[str] = None
    advertise_ip: Optional[str] = None
    port: int = 6274
    readonly: bool = False


@dataclass
class _CoreMCPToolType:
    """
    Represents a callable MCP (Model Context Protocol) tool.

    Attributes:
        name (str): Unique tool name as exposed to MCP clients.
        description (str): Short human-readable description of the tool's purpose.
        input_schema (dict[str, Any]): JSON Schema describing the tool's expected input
            parameters (used for validation and discovery in MCP clients).
            Async or sync callable that executes the tool logic.
            Accepts a dictionary of parameters matching `input_schema` and returns
            a result dictionary (arbitrary structure, but JSON-serializable).

    Methods:
        call(params):
            Executes the tool handler with the given parameters.
            Supports both asynchronous and synchronous handlers.
    """
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    path: str


class CoreMCPService:
    def __init__(self, project_data: Optional[dict] = None,
                 tools_prefix: str = "",
                 patch_vscode_config: bool = False, show_usage_examples: bool = True) -> None:

        """
        Initialize MCP server state and register routes.
        - Loads configuration (host/port).
        - Prepares the aiohttp app and registers command/tool routes.
        - Adds health and tool-list endpoints.
        - Registers the module with telemetry/registry.
        Args:
            project_data (Any): Project data (json parsed data).
            tools_prefix (str): Prefix added to all published tools.
            patch_vscode_config (bool): Maintain VSCode 'mcp.json' file automatically.
            show_usage_examples (bool): Show several 'curl' examples.
        """

        self._single_flight = asyncio.Semaphore(1)  # Single-flight across the whole workspace
        self._current = None  # (tool_name, started_at) for status/telemetry

        self._mcp_config = _CoreMCPConfigType()
        self._logger = CoreMCPLogger("MCP")
        self._shutdown_event = asyncio.Event()
        self._tools_registry: dict[str, _CoreMCPToolType] = {}
        self._mcp_server_name: Optional[str] = None
        self._mcp_server_version: Optional[str] = None
        self._project_data: Optional[dict] = project_data
        self._patch_vscode_config: bool = patch_vscode_config
        self._tool_prefix: str = tools_prefix
        self._show_usage_examples: bool = show_usage_examples
        self._mcp_server_bind_address: Optional[str] = None
        self._mcp_server_port: Optional[int] = 0
        self._shutting_down: bool = False
        self._log_request: bool = False
        self._brutal_termination: bool = False
        self._project_base_path: str = os.getcwd()

        if not isinstance(project_data, dict):
            raise TypeError("project_data must be a dict")

        self._mcp_server_name = self._project_data.get("project_name", "MCP service")
        self._mcp_server_version = self._project_data.get("version", "1.0.0")
        self._commands_data: Optional[dict[str, Any]] = self._project_data.get("commands", {})

        # Port and optional host bind address
        self._mcp_server_port = self._project_data.get("mcp_server_port", self._mcp_config.port)
        self._mcp_config.port = self._mcp_server_port
        self._mcp_server_bind_address = self._project_data.get("mcp_server_bind_address")

        self._app = web.Application()

        # Register all tool routes derived from commands metadata
        self._register_all_commands()

        # Manual endpoints
        self._app.router.add_get("/status", self._status_handler)
        self._app.router.add_post("/shutdown", self._shutdown_handler)
        self._app.router.add_get("/sse", self._sse_handler)
        self._app.router.add_post("/message", self._rpc_handler)
        self._app.router.add_get("/help", self._help_handler)

        # HTTP (streamable) at base URL:
        self._app.router.add_post("/", self._rpc_handler)

        # SSE at base URL:
        self._app.router.add_get("/", self._sse_handler)

    @staticmethod
    def _log_line(msg: str, level: str = "info", **_ignored) -> None:
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            line = f"\r{ts} [{level.title():<8}] {msg}\n".encode()
            os.write(1, line)  # bypasses Python stream redirection
        except Exception as e:
            os.write(1, f"MCP Service log error: {e!r} | original message: {msg!r}\n".encode())

    async def _status_handler(self, _request):
        """Basic runtime status (no secrets)."""
        return self._json_response({
            "host": self._mcp_config.host,
            "port": self._mcp_config.port,
            "readonly": bool(self._mcp_config.readonly),
            "tool_count": sum(
                1 for k, v in self._commands_data.items() if not v.get("hidden")
            )
        })

    async def _shutdown_handler(self, _request: web.Request) -> web.Response:
        """
        Initiate a graceful shutdown of the MCP server.
        Returns:
            200 OK with {"status": "shutting_down"} when accepted.
            403 if server is in read-only mode.
        """
        if getattr(self._mcp_config, "readonly", False):
            return self._json_response({"error": "readonly mode"}, status=403)

        # Signal the run loop to exit; cleanup happens in _run_sse()'s finally block.
        self._shutdown_event.set()
        return self._json_response({"status": "shutting_down"})

    async def _help_handler(self, _request: web.Request) -> web.Response:
        """
        Return centralized help data for all commands in JSON format.
        """
        # TODO: Implement
        try:
            raise RuntimeError("Not implemented")

        except json.JSONDecodeError as e:
            self._log_line(f"Invalid JSON in commands metadata: {e}", level="error")
            return self._json_response({"error": "Internal error: invalid help data"}, status=500)

    async def _broadcast(self, obj: dict[str, Any]) -> None:
        """
        Broadcast a JSON-serializable object to all connected SSE clients.
        Args:
            obj (dict[str, Any]): The message payload to send. Must be JSON-serializable.
        Behavior:
            - Encodes the object as compact JSON (no extra whitespace).
            - Frames the data per SSE spec: prefix with "data: " and terminate
              with a double newline.
            - Attempts to send to all active SSE clients; one failing client will
              not disrupt others (exceptions are suppressed).
            - If the client stream supports `.flush()`, it is called after writing.
            - Sending is asynchronous: writes are awaited (or scheduled with
              `asyncio.create_task`) so the server does not block.

        Notes:
            - This is a best-effort broadcast; slow or disconnected clients may
              miss events if they cannot keep up.
            - Assumes `self._sse_clients` is a set of `aiohttp.web.StreamResponse`
              instances that have been prepared by `_sse_handler()`.
        """
        if not hasattr(self, "_sse_clients"):
            return
        frame = (b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n\n")
        for ws in list(self._sse_clients):
            with contextlib.suppress(Exception):
                await ws.write(frame)
                if hasattr(ws, "flush"):
                    await ws.flush()

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        """
        Handle a Server-Sent Events (SSE) client connection.
        Behavior:
            - Prepares an SSE-compatible HTTP response with required headers.
            - Adds the connection to `self._sse_clients` for use by `_broadcast()`.
            - Sends an initial `: connected` comment to confirm the stream is active.
            - Periodically sends a `heartbeat` event every 15 seconds until shutdown.
            - Suppresses all exceptions from the write loop to avoid noisy disconnect errors.
            - Removes the connection from the active client set on exit.
        Args:
            request (web.Request): The aiohttp request object.
        Returns:
            web.StreamResponse: The prepared SSE response that will remain open
            until the client disconnects or the server shuts down.
        """
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(request)

        if not hasattr(self, "_sse_clients"):
            self._sse_clients = set()
        self._sse_clients.add(resp)

        with contextlib.suppress(Exception):
            # Send initial connection comment
            await resp.write(b": connected\n\n")
            if hasattr(resp, "flush"):
                await resp.flush()

            # Periodic heartbeat
            while not self._shutdown_event.is_set():
                await asyncio.sleep(15)
                await resp.write(b"event: heartbeat\ndata: {}\n\n")
                if hasattr(resp, "flush"):
                    await resp.flush()

        self._sse_clients.discard(resp)
        return resp

    async def _rpc_handler(self, request: web.Request) -> web.Response:
        """
        JSON-RPC endpoint for MCP over HTTP POST.
        Supports:
          - Single requests and batches per JSON-RPC 2.0
          - Methods: initialize, tools/list, tools/call, ping
          - Notifications (no 'id'): returns 200 with {} for VSCode.
        Error behavior:
          - Always HTTP 200 with JSON-RPC error envelope (-32700, -32600, -32603).
          - Never lets exceptions bubble to aiohttp (prevents HTTP 500).
        """

        # Helper builders (avoid repeating structure everywhere)
        def _jr_ok(_jid: Any, _result: Any) -> dict[str, Any]:
            return {"jsonrpc": "2.0", "id": _jid, "result": _result}

        def _jr_err(_jid: Any, _code: int, _message: str, _data: Any = None) -> dict[str, Any]:
            err: dict[str, Any] = {"code": _code, "message": _message}
            if _data is not None:
                err["data"] = _data
            return {"jsonrpc": "2.0", "id": _jid, "error": err}

        with contextlib.suppress(Exception):
            self._log_line(msg="POST /message", level="debug")

        # Method gate first (cheap)
        if request.method != "POST":
            error_body = _jr_err(jid=None, code=-32600, message="method not allowed")
            return web.json_response(error_body)

        # Read body defensively
        raw = await request.read()
        if not raw:
            error_body = _jr_err(jid=None, code=-32600, message="Empty request")
            return web.json_response(error_body)

        try:
            payload: Any = json.loads(raw.decode("utf-8"))
        except Exception as e:
            error_body = _jr_err(jid=None, code=-32700, message="Parse error", data=str(e))
            return web.json_response(error_body)

        with contextlib.suppress(Exception):
            self._log_line(msg="RPC handler got payload", level="debug")

            if self._log_request:
                pretty = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
                self._log_line(msg=f"Request:\n{pretty}", level="debug")

        async def _handle_one(msg: dict[str, Any]) -> Optional[dict[str, Any]]:
            """
            Handle a single JSON-RPC message. Returns a response dict,
            or None if input was a notification (no 'id').
            """
            jid = msg.get("id", None)
            is_notification = jid is None
            method = msg.get("method")
            params = msg.get("params") or {}

            with contextlib.suppress(Exception):
                self._log_line(msg=f"Incoming method: {method}, id: {jid}", level="debug")

            # Inline helpers return proper envelopes only when id is present
            if is_notification:
                def ok(_: Any) -> None:  # type: ignore[override]
                    return None

                def make_error(_: int, __: str) -> None:  # type: ignore[override]
                    return None
            else:
                def ok(_result: Any) -> dict[str, Any]:
                    return _jr_ok(jid, _result)

                def make_error(_code: int, _message: str) -> dict[str, Any]:
                    return _jr_err(jid, _code, _message)

            if not isinstance(msg, dict) or not isinstance(method, str):
                return make_error(-32600, "invalid request")
            if not isinstance(params, dict):
                return make_error(-32602, "invalid params")

            # -----------------------------------------------------------------
            #
            # Handle common MCP service methods
            #
            # -----------------------------------------------------------------

            try:
                if method == "initialize":
                    client_proto = params.get("protocolVersion", "2025-06-18")
                    info = {
                        "protocolVersion": client_proto,
                        "serverInfo": {
                            "name": str(self._mcp_server_name),
                            "version": str(self._mcp_server_version),
                        },
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                            "prompts": {}
                        },
                    }
                    self._log_line(msg=f"Handled 'initialize'", level="debug")
                    return ok(info)

                # -----------------------------------------------------------------

                elif method == "tools/list":
                    return ok(self._rpc_tools_list())

                # -----------------------------------------------------------------

                elif method == "help":
                    result = await self._help_handler_rpc(params)
                    return ok(result)

                # -----------------------------------------------------------------

                elif method == "resources/list":

                    resources = []
                    for tool_name, tool_info in self._project_data.get("commands", {}).items():
                        resource_path = tool_info.get("resource")
                        if not resource_path:
                            continue
                        abs_path = os.path.join(self._project_base_path, resource_path)
                        uri = f"file://{os.path.abspath(abs_path)}"
                        resources.append({
                            "name": tool_name,
                            "uri": uri,
                            "mimeType": "text/markdown"
                        })
                    return ok({"resources": resources})

                # -----------------------------------------------------------------

                elif method == "resources/read":

                    uri = params.get("uri")
                    if not uri or not uri.startswith("file://"):
                        return make_error(-32602, f"Invalid or missing URI: {uri}")

                    parsed = urlparse(uri)
                    path = unquote(parsed.path)
                    query_params = dict(parse_qsl(parsed.query))

                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            text = f.read()
                    except Exception as read_error:
                        return make_error(-32000, f"Failed to read resource {uri}: {read_error}")

                    # --- Dynamic substitution demo: used in templates  ---
                    if query_params:
                        # Append a small note at the bottom of the Markdown
                        args_str = ", ".join(f"{k}={v}" for k, v in query_params.items())
                        text += f"\n\n---\n*Template arguments applied:* {args_str}\n"

                    return ok({
                        "contents": [
                            {"uri": uri, "text": text}
                        ]
                    })
                # -----------------------------------------------------------------

                elif method in ("templates/list", "resources/templates/list"):
                    resource_templates = []
                    base = os.path.abspath(os.path.join(self._project_base_path, "resources"))

                    for name, tmpl in self._project_data.get("templates", {}).items():
                        args = []
                        arg_name = ""
                        for arg_name, default in tmpl.get("args", {}).items():
                            args.append({
                                "name": arg_name,
                                "description": f"Argument for {tmpl.get('command')}",
                                "default": default
                            })

                        # Build a simple uriTemplate using the resource path
                        resource_path = tmpl.get("command").replace("tool_", "tool_") + ".md"
                        uri_template = f"file://{base}/{resource_path}?{arg_name}={{{arg_name}}}"

                        resource_templates.append({
                            "name": name,
                            "description": tmpl.get("description", ""),
                            "uriTemplate": uri_template,
                            "arguments": args
                        })

                    return ok({"resourceTemplates": resource_templates})


                # -----------------------------------------------------------------

                elif method == "tools/call":
                    tool_name = params.get("name", "<?>")
                    with contextlib.suppress(Exception):
                        self._log_line(msg=f"Calling tool: {tool_name} with: {params}", level="debug")

                    # Single flight: try to acquire immediately; reject if busy
                    try:
                        await asyncio.wait_for(self._single_flight.acquire(), timeout=0.001)
                    except asyncio.TimeoutError:
                        return make_error(
                            AUTO_FORGE_BUSY_CODE,
                            "Busy: another tool is currently running in this workspace")

                    self._current = (tool_name, asyncio.get_running_loop().time())
                    try:
                        result = await self._rpc_tools_call(params)
                        with contextlib.suppress(Exception):
                            self._log_line(
                                msg=f"Tool '{tool_name}' result keys: {list(result.keys())}",
                                level="debug"
                            )
                            await self._broadcast({
                                "jsonrpc": "2.0",
                                "method": "tools/result",
                                "params": {"name": tool_name, "result": result},
                            })

                            # Adapt result for MCP inspector
                            if isinstance(result, str):
                                wrapped = {
                                    "isError": False,
                                    "content": [{"type": "text", "text": str(result)}]
                                }
                            else:
                                # Dump dicts cleanly to text
                                wrapped = {
                                    "isError": False,
                                    "content": [{
                                        "type": "text",
                                        "text": "\n" + json.dumps(result, indent=2)
                                    }]
                                }

                        return ok(wrapped)

                    finally:
                        self._current = None
                        self._single_flight.release()

                # -----------------------------------------------------------------

                elif method == "ping":
                    return ok({})

                return make_error(-32601, f"unknown method: {method}")

            except KeyError as ke:
                return make_error(-32601, str(ke))
            except Exception as ex:
                with contextlib.suppress(Exception):
                    await self._broadcast({"jsonrpc": "2.0", "method": "tools/error", "params": {"error": str(ex)}})
                    self._log_line(f"_handle_one crash: {ex!r}", level="error")
                return make_error(-32603, "Internal error")

        # Single vs batch
        try:
            if isinstance(payload, list):
                # Empty batch is invalid
                if len(payload) == 0:
                    error_body = _jr_err(jid=None, code=-32600, message="invalid request (empty batch)")
                    return web.json_response(error_body)

                if len(payload) > AUTO_FORGE_MAX_BATCH_MCP_COMMANDS:
                    error_body = _jr_err(jid=None, code=-32600, message="batch too large")
                    return web.json_response(error_body)

                replies: list[dict[str, Any]] = []
                for item in payload:
                    resp = await _handle_one(item if isinstance(item, dict) else {})
                    if resp is not None:
                        replies.append(resp)

                if replies:
                    return web.json_response(replies)
                # All were notifications, return {} (VS Code compat)
                return web.json_response({})

            # Single message
            if not isinstance(payload, dict):
                error_body = _jr_err(jid=None, code=-32600, message="invalid request")
                return web.json_response(error_body)

            reply = await _handle_one(payload)
            if reply is None:
                return web.json_response({})
            return web.json_response(reply)

        except Exception as e:
            with contextlib.suppress(Exception):
                self._log_line(f"/message handler crash (outer): {e!r}", level="error")
            error_body = _jr_err(jid=None, code=-32603, message="Internal error")
            return web.json_response(error_body)

    @staticmethod
    async def _help_handler_rpc(_params: dict[str, Any]) -> dict[str, Any]:
        """
        JSON-RPC: Return centralized help data for all commands (or a specific one).
        """
        # TODO: Implement
        return {"error": "Help metadata not available"}

    def _add_tool(self, tool: _CoreMCPToolType):
        """
        Register a new MCP tool in the server's tool registry.
        Args:
            tool (_CoreMCPToolType): The tool instance to register. The `name`
                attribute is used as the registry key.
        Notes:
            - If a tool with the same name already exists, it will be overwritten.
            - Registered tools are discoverable via `tools/list` and callable via
              `tools/call` in the MCP JSON-RPC API.
        """
        self._tools_registry[tool.name] = tool

    @staticmethod
    def _json_response(data: Any, status: int = 200) -> web.Response:
        """
        Create a consistent JSON response with indentation.
        Args:
            data (dict): The data to serialize and return as JSON.
        Returns:
            web.Response: A JSON response with pretty indentation.
        """
        return web.json_response(
            data,
            status=status,
            dumps=lambda x: json.dumps(x, indent=2, ensure_ascii=False) + "\n",
        )

    async def _run_one_cmdline_async(self, line: str) -> dict[str, Any]:
        """
        Run a single command line asynchronously inside the build shell.

        Behavior:
            - Executes the given `line` using subprocess.
            - Captures logs incrementally and streams them to SSE clients as
              {"event": "log", "data": "<line>"} while the command is running.
            - Returns a final JSON-compatible dict containing:
                * status  : exit status (int)
                * logs    : full list of logs (list[str])
                * summary : execution summary (str)
                * output  : optional parsed output (dict or list, if applicable)
            - Emits a final {"event": "done", ...} broadcast with the same result.
        """
        logs: list[str] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(line),
                cwd=Path.cwd(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as execute_error:
            raise RuntimeError(f"Failed to launch {line}: {execute_error}")

        # Read output line by line
        async for raw_line in proc.stdout:
            decoded: str = raw_line.decode(errors="replace")
            text = decoded.rstrip()
            logs.append(text)

            # Broadcast incremental log line
            with contextlib.suppress(Exception):
                await self._broadcast({"event": "log", "data": text})

        # Wait for process to complete
        status = await proc.wait()

        # Final result object
        result: dict[str, Any] = {
            "status": status,
            "logs": logs,
            "summary": f"Executed: {line} (exit {status})",
        }

        # Broadcast completion
        with contextlib.suppress(Exception):
            await self._broadcast({"event": "done", **result})

        return result

    def _register_all_commands(self) -> None:
        """
        Register all loaded commands both as MCP tools (for SSE JSON-RPC)
        and, optionally, as REST POST endpoints under /tool/<name>.

        Commands marked as hidden or of type 'NAVIGATE' are skipped.
        Tool names are prefixed and must match MCP naming rules [a-z0-9_-].
        """

        if not isinstance(self._commands_data, dict) or not self._commands_data:
            raise TypeError("commands_data must be a non-empty dict")

        for key, entry in self._commands_data.items():

            tool_name = f"{self._tool_prefix}{key}"
            if not re.fullmatch(r"[a-z0-9_-]+", tool_name):
                raise RuntimeError(f"Invalid MCP tool name: {tool_name}")

            description = entry.get("description") or f"Run '{key}' tool."

            _tools_path = entry.get("path")
            if not _tools_path:
                raise RuntimeError(f"Invalid 'path' in MCP tool entry: {tool_name}")

            # Wrap into a tuple so multiple paths could be supported later
            _cmds_tuple = (_tools_path,)

            # MCP-compatible JSON schema
            input_schema = {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional command-line arguments",
                    }
                },
                "required": [],
                "additionalProperties": False,
            }

            # Register as MCP tool
            self._add_tool(_CoreMCPToolType(
                name=tool_name,
                description=description,
                input_schema=input_schema,
                path=_tools_path
            ))

            # Legacy REST fallback
            def make_handler(_cmds=_cmds_tuple):
                async def handler(request):
                    payload = {}
                    with contextlib.suppress(Exception):
                        payload = await request.json()

                    raw_args = payload.get("args", [])
                    arg_list = []

                    # Normalize args into a list of strings
                    if isinstance(raw_args, str):
                        arg_list = [raw_args]
                    elif isinstance(raw_args, list):
                        arg_list = [str(a) for a in raw_args]

                    extra = " ".join(arg_list).strip()

                    lines = []
                    for raw in _cmds:
                        line = f"{raw} {extra}" if extra else raw
                        line = os.path.expandvars(line)
                        lines.append(line)

                    try:
                        results = [await self._run_one_cmdline_async(line) for line in lines]
                        return self._json_response({"results": results})
                    except Exception as e:
                        return self._json_response({"error": str(e)}, status=500)

                return handler

            self._app.router.add_post(f"/tool/{tool_name}", make_handler())

    async def _rpc_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Invoke a registered MCP tool as a subprocess by name.
        Args:
            params (dict[str, Any]): JSON-RPC parameters with:
                - "name" (str): The registered tool's name.
                - "arguments" (dict): Arguments to pass to the tool.
                  Must conform to the tool's `input_schema`.
        Returns:
            dict[str, Any]: The tool's result payload, as returned by
            `_run_one_cmdline_async` (JSON-serializable).
        """
        name = params.get("name")
        arguments = params.get("arguments") or {}

        tool = self._tools_registry.get(name)
        if not tool:
            raise KeyError(f"unknown tool: {name}")

        # Build the command line
        raw_args = arguments.get("args", [])
        arg_list = [raw_args] if isinstance(raw_args, str) \
            else [str(a) for a in raw_args] if isinstance(raw_args, list) else []

        tool_path = str(Path(tool.path).resolve())
        cmdline = " ".join([tool_path] + arg_list)

        # Run via service method
        return await self._run_one_cmdline_async(cmdline)

    def _rpc_tools_list(self) -> dict[str, Any]:
        """
        List all registered MCP tools.
        Returns:
            dict[str, Any]: A dictionary with key "tools" containing a list of
            tool descriptors, where each descriptor includes:
                - "name" (str): Tool name.
                - "description" (str): Tool description.
                - "inputSchema" (dict): JSON Schema for the tool's input.
        """
        tools = [{
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema
        } for t in self._tools_registry.values()]

        return {"tools": tools}

    async def _run_sse(self):
        """
        Internal async loop that configures and starts the SSE server.

        - Uses `aiohttp.web.AppRunner` to attach `self._app` to an HTTP server.
        - Binds to the configured host and port.
        - Stays alive indefinitely, sleeping in 1-hour intervals until stopped.
        - Ensures cleanup of resources on shutdown.
        """
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, self._mcp_config.host, self._mcp_config.port)
        await site.start()

        try:
            await self._shutdown_event.wait()  # Block until told to exit
        except asyncio.CancelledError:
            # Handles task.cancel() if loop is being cancelled
            pass
        finally:
            await runner.cleanup()
            # Give aiohttp tasks a chance to settle
            await asyncio.sleep(1)

    @staticmethod
    def _remove_vscode_config(base_path: Optional[Union[Path, str]],
                              host: str,
                              port: int,
                              server_name: str) -> bool:
        """
        Quietly remove a server entry from an existing VS Code MCP config.
        Args:
            base_path (Union[Path, str], optional):
                Workspace base directory containing the .vscode folder.
                If None, use the solution workspace (PROJ_WORKSPACE).
            host (str): Host IP address to match in the config.
            port (int): Host port to match in the config.
            server_name (str): Server key to remove (default: "autoforge").

        Returns:
            bool: True if the config was updated or nothing needed removal,
                  False if an error occurred.
        """
        if base_path is None:
            base_path = os.getcwd()

        vscode_dir = Path(base_path).expanduser().resolve() / ".vscode"
        config_path = vscode_dir / "mcp.json"

        if not config_path.exists():
            return True  # nothing to remove

        with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError, OSError):
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            url_to_remove = f"http://{host}:{port}"
            servers = data.get("servers", {})

            if server_name in servers:
                if servers[server_name].get("url") == url_to_remove:
                    del servers[server_name]

            # Clean up if servers now empty
            if not servers:
                data.pop("servers", None)

            with contextlib.suppress(Exception):
                config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                return True

        return False

    @staticmethod
    def _generate_vscode_config(base_path: Optional[Union[Path, str]],
                                host: str,
                                port: int,
                                server_name: str,
                                overwrite_existing: bool = True,
                                create_parents: bool = False,
                                ensure_inputs: bool = True) -> bool:
        """
        Generate/merge a VS Code MCP config without clobbering existing servers.
        Args:
            base_path: Base directory where '.vscode/mcp.json' will be written.
                         If None, uses self._variables['PROJ_WORKSPACE'].
            host: IP for the SSE server.
            port: Port for the SSE server.
            server_name: Key under "servers" to write/update (default: "autoforge").
            overwrite_existing: If True, overwrite the existing <server_name> entry
                                if present. If False, only add it if missing.
            create_parents: If True, create <dir>/.vscode if missing.
            ensure_inputs: If True, add a minimal "inputs" section when absent.

        Returns:
            bool: True if the config file was written/updated, False otherwise.
        """
        if base_path is None:
            base_path = os.getcwd()

        base_dir = Path(base_path).expanduser().resolve()

        vscode_dir = base_dir / ".vscode"
        config_path = vscode_dir / "mcp.json"

        if create_parents:
            vscode_dir.mkdir(parents=True, exist_ok=True)

        data: Optional[dict] = None

        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (JSONDecodeError, UnicodeDecodeError):
                with contextlib.suppress(Exception):
                    config_path.rename(config_path.with_suffix(".json.bak"))
                data = {}

        if data is None:
            data = {}

        servers = data.setdefault("servers", {})
        new_entry = {"type": "sse", "url": f"http://{host}:{port}"}

        if server_name not in servers or overwrite_existing or servers[server_name] != new_entry:
            servers[server_name] = new_entry
        else:
            return False  # no change needed

        if ensure_inputs and "inputs" not in data:
            data["inputs"] = [
                {"id": "args", "type": "promptString", "description": "Extra arguments"}]

        with contextlib.suppress(Exception):
            config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True

        return False

    # noinspection SpellCheckingInspection
    @staticmethod
    def _greetings(host: str, port: int, server_name: str, show_examples: bool = False, host_bind_address: str = None):
        """
        Display greetings and optionally example 'curl' commands that can be copied and run directly
        in a console window to interact with the SSE server.
        Args:
            host (str): Host/IP address of the MCP server.
            port (int): TCP port where the MCP service is listening.
            server_name (str): Name of the MCP service
            show_examples: If True, show example commands.
            host_bind_address (ooptional str): Bind address to bind to the MCP server.
        """

        base = f"http://{host}:{port}"
        title = "MCP SSE Service Info:"

        # Greetings
        print(f"\033]0;\007\033[2J\033[3J\033[H", end="")
        print(f"\n{title}\n{'-' * len(title)}\n"
              f"- Base:                  {base}\n"
              f"- SSE stream:            {base}/sse\n"
              f"- JSON-RPC message bus:  {base}/message\n"
              f"- Name:                  {server_name}")
        if isinstance(host_bind_address, str):
            print(f"- Bind address:          {host_bind_address}")

        if show_examples:
            # Examples
            print("\nExample commands you can run in another shell:")

            print("\n1. Listen for SSE broadcasts:")
            print(f"   curl -s -N --noproxy {host} {base}/sse")

            print("\n2. List available tools:")
            print(f"   curl -s --noproxy {host} "
                  "-H \"Content-Type: application/json\" "
                  "-d \"{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":1,\\\"method\\\":\\\"tools/list\\\",\\\"params\\\":{}}\" "
                  f"{base}/message | jq")

            print("\n3. Execute tool 'tool_a' with argument 'Allice':")
            print(f"   curl -s --noproxy {host} "
                  "-H \"Content-Type: application/json\" "
                  "-d \"{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":2,\\\"method\\\":\\\"tools/call\\\","
                  "\\\"params\\\":{\\\"name\\\":\\\"tool_a\\\",\\\"arguments\\\":{\\\"args\\\":[\\\"Alice\\\"]}}}\" "
                  f"{base}/message | jq")

            print("\n4. Get help (all commands):")
            print(f"   curl -s --noproxy {host} "
                  "-H \"Content-Type: application/json\" "
                  "-d \"{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":3,\\\"method\\\":\\\"help\\\","
                  "\\\"params\\\":{}}\" "
                  f"{base}/message | jq")

            print("\n5. Get help for a specific command (e.g. busd):")
            print(f"   curl -s --noproxy {host} "
                  "-H \"Content-Type: application/json\" "
                  "-d \"{\\\"jsonrpc\\\":\\\"2.0\\\",\\\"id\\\":4,\\\"method\\\":\\\"help\\\","
                  "\\\"params\\\":{\\\"command\\\":\\\"busd\\\"}}\" "
                  f"{base}/message | jq")

        print("\nRunning... Press Ctrl+C to stop.\n")

    def start(self) -> int:
        """
        Start the MCP server in SSE (Server-Sent Events) mode.
        Attempts to determine the system's primary external IPv4 address
        (non-loopback) by connecting to a known public IP (Google DNS at 8.8.8.8).
        This does not require actual network reachability, no data is sent.
        Runs the asynchronous SSE server loop until interrupted.

        Returns:
            int: 0 if the server started successfully, 1 if an exception occurred.
        """

        def _handle_term_signal():
            self._log_line(msg=f"Interrupted by user, shutting down", level="warning")

            self._shutting_down = True
            self._shutdown_event.set()

            # Cancel running tasks
            for task in asyncio.all_tasks(loop):
                task.cancel()

            # Remove VSCode config if needed
            if self._patch_vscode_config:
                self._remove_vscode_config(
                    base_path=None,
                    host=self._mcp_config.advertise_ip,
                    port=self._mcp_config.port,
                    server_name=self._mcp_server_name, )
            # Terminate
            if self._brutal_termination:
                os.kill(os.getpid(), signal.SIGKILL)
            else:
                loop.stop()

        try:

            # Determine the outward-facing local IP by opening a dummy UDP socket
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                self._mcp_config.advertise_ip = s.getsockname()[0]

            if not isinstance(self._mcp_config.advertise_ip, str):
                raise RuntimeError("Could not determine local IP address")

            # Choose which host address to bind the server to:
            # If a specific bind address is provided in the project JSON, use that.
            # Otherwise, fall back to the discovered local IP.
            # Note: binding to 0.0.0.0 (all interfaces) can sometimes resolve connectivity issues.

            if isinstance(self._mcp_server_bind_address, str):
                self._mcp_config.host = self._mcp_server_bind_address
            else:
                self._mcp_config.host = self._mcp_config.advertise_ip

            if not isinstance(self._mcp_config.host, str):
                raise RuntimeError("Failed to resolve a valid host address")

            # Create VSCode 'mcp.json' file in the solution workspace
            if self._patch_vscode_config:
                self._generate_vscode_config(base_path=None, host=self._mcp_config.advertise_ip,
                                             port=self._mcp_config.port, server_name=self._mcp_server_name,
                                             overwrite_existing=True, create_parents=True)

            # Show welcome message and usage examples
            self._greetings(host=self._mcp_config.advertise_ip, port=self._mcp_config.port,
                            server_name=self._mcp_server_name, show_examples=self._show_usage_examples,
                            host_bind_address=self._mcp_server_bind_address)

            # Prepare asyncio loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            # Attach signal handlers so Ctrl+C triggers shutdown cleanly
            for sig in (signal.SIGINT, signal.SIGTERM):
                # noinspection PyTypeChecker
                loop.add_signal_handler(sig, _handle_term_signal)

            # Run the SSE server
            if loop.is_running():
                asyncio.create_task(self._run_sse())
            else:
                loop.run_until_complete(self._run_sse())

            return 0

        except KeyboardInterrupt:
            return 0
        except Exception as e:
            if self._shutting_down:
                self._log_line(msg=f"MCP server terminated", level="debug")
                print()
                return 0
            self._log_line(msg=f"MCP Error: {e}", level="error")
            return 1
