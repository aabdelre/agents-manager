"""A small stdio MCP server. No network listener and no shell-based RPC."""

from __future__ import annotations

import json
import sys
import time

from chief.store import Store
from chief.tmux import Tmux


def tool(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
            "additionalProperties": False,
        },
    }


STRING = {"type": "string"}
TOOLS = [
    tool("list_sessions", "List all workstreams with status, summaries, blockers and unread message counts."),
    tool(
        "spawn",
        "Chief only: delegate a task to a new persistent Claude session in its own tmux session.",
        {"name": STRING, "task": STRING, "cwd": STRING, "model": STRING},
        ("name", "task", "cwd"),
    ),
    tool(
        "send",
        "Send a durable instruction. Agents may send to chief; chief may send to any workstream.",
        {"to": STRING, "message": STRING},
        ("to", "message"),
    ),
    tool(
        "probe",
        "Chief only: request a concise progress report from one workstream or all of them.",
        {"session": STRING},
        ("session",),
    ),
    tool(
        "inspect",
        "Chief only: read the actual terminal output of a workstream, including permission prompts.",
        {"session": STRING},
        ("session",),
    ),
    tool("inbox", "Read your unacknowledged inbox. Reading is not acknowledgement."),
    tool(
        "await_updates",
        "Chief only: wait up to 30 seconds for new messages without typing into the terminal. "
        "Use while coordinating active assignments; acknowledge returned messages.",
    ),
    tool(
        "ack",
        "Acknowledge message IDs after understanding them. Duplicate acknowledgements are safe.",
        {"ids": {"type": "array", "items": {"type": "integer"}}},
        ("ids",),
    ),
    tool(
        "report",
        "Publish a concise progress report to chief and update the sidebar. Todos are HUMAN actions.",
        {
            "summary": STRING,
            "next_step": STRING,
            "blocker": STRING,
            "todos": {"type": "array", "items": STRING},
        },
        ("summary",),
    ),
    tool(
        "add_todo",
        "Add an action the HUMAN needs to take for a workstream.",
        {"text": STRING, "session": STRING},
        ("text",),
    ),
    tool("list_todos", "List outstanding human action items for all workstreams."),
]


class Protocol:
    def __init__(self, store: Store, identity):
        self.store = store
        self.identity = identity
        self.row = store.session(identity)
        self.tmux = Tmux(store)

    def chief_only(self):
        if self.row["role"] != "chief":
            raise ValueError("Only the chief can perform this operation.")

    def call(self, name, args):
        schema = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
        if schema is None:
            raise ValueError(f"Unknown tool: {name}")
        if not isinstance(args, dict) or set(args) - schema["properties"].keys():
            raise ValueError("Unexpected tool arguments")
        if not set(schema["required"]) <= args.keys():
            raise ValueError("Missing required tool arguments")
        for key, value in args.items():
            kind = schema["properties"][key]["type"]
            if kind == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if kind == "array" and (
                not isinstance(value, list)
                or any(type(item) is not (int if key == "ids" else str) for item in value)
            ):
                raise ValueError(f"Invalid array: {key}")
        if name == "list_sessions":
            return self.store.sessions()
        if name == "spawn":
            self.chief_only()
            self.store.create(args["name"], args["cwd"], args["task"], args.get("model"), self.row["demo"])
            self.tmux.ensure(args["name"])
            return self.store.session(args["name"])
        if name == "send":
            if self.identity != "chief" and args["to"] != "chief":
                raise ValueError("Workstreams report to chief. Ask chief to coordinate another workstream.")
            message_id = self.store.send(self.identity, args["to"], args["message"])
            return {"id": message_id, "status": "queued"}
        if name == "probe":
            self.chief_only()
            targets = (
                [r["name"] for r in self.store.sessions() if r["role"] != "chief"]
                if args["session"] == "all"
                else [args["session"]]
            )
            return [
                {
                    "session": target,
                    "id": self.store.send(
                        self.identity,
                        target,
                        "Report current progress, evidence, next step, and blockers using the report tool. Continue your assignment.",
                        "probe",
                    ),
                }
                for target in targets
            ]
        if name == "inspect":
            self.chief_only()
            return {"session": args["session"], "terminal": self.tmux.capture(args["session"])}
        if name == "inbox":
            return self.store.inbox(self.identity)
        if name == "await_updates":
            self.chief_only()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                messages = self.store.deliver(self.identity)
                if messages:
                    return messages
                time.sleep(0.25)
            return {"status": "No new updates in 30 seconds. Workstreams remain active."}
        if name == "ack":
            self.store.ack(self.identity, args["ids"])
            return {"acknowledged": args["ids"]}
        if name == "report":
            self.store.report(self.identity, **args)
            return {"status": "reported"}
        if name == "add_todo":
            target = args.get("session", self.identity)
            if self.identity != "chief" and target != self.identity:
                raise ValueError("Agents may add action items only to their own workstream.")
            self.store.add_todo(target, args["text"])
            return {"status": "added"}
        if name == "list_todos":
            return self.store.todos()
        raise ValueError(f"Unknown tool: {name}")

    def dispatch(self, request):
        if not isinstance(request, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
        if "id" not in request:
            return None
        response = {"jsonrpc": "2.0", "id": request["id"]}
        method = request.get("method")
        if method == "initialize":
            requested = request.get("params", {}).get("protocolVersion", "2024-11-05")
            version = requested if requested in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2025-06-18"
            response["result"] = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "chief", "version": "0.1.0"},
            }
        elif method == "ping":
            response["result"] = {}
        elif method == "tools/list":
            response["result"] = {
                "tools": [
                    t
                    for t in TOOLS
                    if self.identity == "chief"
                    or t["name"] not in {"spawn", "probe", "inspect", "await_updates"}
                ]
            }
        elif method == "tools/call":
            try:
                params = request.get("params", {})
                result = self.call(params.get("name"), params.get("arguments", {}))
                response["result"] = {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
                }
            except (ValueError, RuntimeError, OSError) as exc:
                response["result"] = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
        return response

    def serve(self):
        for line in sys.stdin:
            try:
                response = self.dispatch(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if response is not None:
                print(json.dumps(response), flush=True)
