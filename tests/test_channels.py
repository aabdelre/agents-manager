import json
import queue
import sqlite3
import subprocess
import threading

import pytest

from chief.protocol import Protocol
from chief.runtime import claude_environment, handle_hook, prepare
from chief.store import Store
from chief.tmux import Tmux


def test_native_colors_override_inherited_automation_flags():
    original = {
        "NO_COLOR": "1",
        "NODE_DISABLE_COLORS": "1",
        "FORCE_COLOR": "0",
        "TERM": "dumb",
        "PATH": "/usr/bin",
        "CLAUDECODE": "1",
        "MY_SETTING": "unchanged",
    }
    result = claude_environment(original)
    assert "NO_COLOR" not in result and "NODE_DISABLE_COLORS" not in result and "CLAUDECODE" not in result
    assert result["FORCE_COLOR"] == "3"
    assert result["TERM"] == "tmux-256color"
    assert result["COLORTERM"] == "truecolor"
    assert result["MY_SETTING"] == "unchanged" and original["NO_COLOR"] == "1"


def test_launch_enables_only_our_native_channel(store):
    args = prepare(store, "chief")
    index = args.index("--dangerously-load-development-channels")
    assert args[index + 1] == "server:chief"
    settings = json.loads((store.home / "sessions/chief/settings.json").read_text())
    assert "--channel" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    mcp = json.loads((store.home / "sessions/chief/mcp.json").read_text())
    assert mcp["mcpServers"]["chief"]["args"][-1] == "--channel"
    assert "--dangerously-skip-permissions" not in args


def test_channel_capabilities_and_handshake(store, monkeypatch):
    channel = Protocol(store, "chief", channel=True)
    result = channel.dispatch({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert result["result"]["capabilities"]["experimental"] == {"claude/channel": {}}
    store.send("product", "chief", "Please make a decision")
    emitted = []
    monkeypatch.setattr(channel, "emit", emitted.append)
    assert channel.push_pending() == 0
    channel.dispatch({"method": "notifications/initialized"})
    assert channel.push_pending() == 1
    assert emitted[0]["params"]["meta"]["sender"] == "product"
    assert "Please make a decision" in emitted[0]["params"]["content"]
    tools = channel.dispatch({"id": 2, "method": "tools/list"})["result"]["tools"]
    assert "await_updates" not in {t["name"] for t in tools}


def test_batches_once_per_connection_and_replay_after_restart(store):
    ids = [store.send("chief", "product", f"Message {n}") for n in range(20)]
    delivered = []
    for _ in range(4):
        delivered.extend(store.deliver("product", channel_token="connection-a"))
    assert [m["id"] for m in delivered] == ids
    assert store.deliver("product", channel_token="connection-a") == []
    store.ack("product", ids[:12])
    replayed = Store(store.home).deliver("product", channel_token="connection-b")
    assert [m["id"] for m in replayed] == ids[12:]


@pytest.mark.parametrize("state", ["idle", "ready", "working", "permission"])
def test_delivery_never_depends_on_focus_or_turn_state(store, state, monkeypatch):
    store.update("product", state=state, attended=1)
    channel = Protocol(store, "product", channel=True)
    channel.ready.set()
    store.send("chief", "product", "Please update")
    emitted = []
    monkeypatch.setattr(channel, "emit", emitted.append)
    assert channel.push_pending() == 1
    assert store.session("product")["state"] == state  # Claude controls scheduling and approvals.
    assert len(emitted) == 1


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"])
def test_hooks_do_not_steal_native_channel_messages(store, event):
    store.send("chief", "product", "Native delivery")
    assert handle_hook(store, "product", {"hook_event_name": event}, channel=True) == {}
    assert store.inbox("product", pending_only=True)


def test_existing_database_migrates_without_losing_messages(store):
    message_id = store.send("chief", "product", "Keep this")
    with sqlite3.connect(store.path) as db:
        db.execute("ALTER TABLE messages DROP COLUMN channel_token")
    upgraded = Store(store.home)
    assert upgraded.deliver("product", channel_token="upgraded")[0]["id"] == message_id


def test_bidirectional_push_over_real_stdio_without_inbox_requests(store):
    processes = []
    outputs = {}
    try:
        for actor in ("chief", "product"):
            process = subprocess.Popen(
                Tmux(store).command("_mcp", actor, "--channel"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            processes.append(process)
            lines = outputs[actor] = queue.Queue()

            def read_lines(stream=process.stdout, inbox=lines):
                for line in stream:
                    inbox.put(json.loads(line))

            threading.Thread(target=read_lines, daemon=True).start()
            process.stdin.write(json.dumps({"id": 1, "method": "initialize"}) + "\n")
            process.stdin.flush()
            assert lines.get(timeout=5)["id"] == 1
            process.stdin.write(json.dumps({"method": "notifications/initialized"}) + "\n")
            process.stdin.flush()

        chief, product = processes
        chief.stdin.write(
            json.dumps(
                {
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "send",
                        "arguments": {"to": "product", "message": "Please investigate"},
                    },
                }
            )
            + "\n"
        )
        chief.stdin.flush()
        assert outputs["chief"].get(timeout=5)["id"] == 2
        event = outputs["product"].get(timeout=5)
        assert event["method"] == "notifications/claude/channel"
        assert "Please investigate" in event["params"]["content"]

        product.stdin.write(
            json.dumps(
                {
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "report", "arguments": {"summary": "Investigation finished"}},
                }
            )
            + "\n"
        )
        product.stdin.flush()
        assert outputs["product"].get(timeout=5)["id"] == 2
        response_event = outputs["chief"].get(timeout=5)
        assert response_event["params"]["meta"]["sender"] == "product"
        assert "Investigation finished" in response_event["params"]["content"]
    finally:
        for process in processes:
            process.stdin.close()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
