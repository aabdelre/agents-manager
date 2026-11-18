import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from chief.protocol import Protocol
from chief.runtime import handle_hook, prepare
from chief.store import Store
from chief.tmux import Tmux


def test_durable_delivery_ack_and_replay(store):
    message_id = store.send("chief", "product", "Build it")
    assert store.deliver("product")[0]["id"] == message_id
    assert not store.deliver("product")
    reopened = Store(store.home)
    assert reopened.deliver("product", replay=True)[0]["id"] == message_id
    reopened.ack("chief", [message_id])
    assert reopened.inbox("product")  # Recipient scoping prevents another session's acknowledgement.
    reopened.ack("product", [message_id])
    reopened.ack("product", [message_id])
    assert not reopened.inbox("product")
    assert reopened.inbox("product", include_acked=True)


def test_concurrent_writers_and_claimers(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda n: store.send("chief", "product", f"Task {n}"), range(40)))
        deliveries = list(pool.map(lambda _: store.deliver("product"), range(8)))
    claimed = [message["id"] for batch in deliveries for message in batch]
    assert len(claimed) == len(set(claimed)) == 40
    assert set(ids) == set(claimed)


def test_report_updates_sidebar_and_chief_mailbox(store):
    store.report("product", "Plan completed", "Review scope", "", ["Approve scope"])
    assert store.session("product")["summary"] == "Plan completed"
    assert json.loads(store.inbox("chief")[0]["body"])["next"] == "Review scope"
    store.add_todo("product", "Approve scope")
    assert len(store.todos()) == 1
    store.complete_todo(store.todos()[0]["id"])
    assert store.todos() == []


@pytest.mark.parametrize("name", ["../escape", "$(touch nope)", "two words", "-chief", "Chief", "a" * 33])
def test_invalid_names_are_rejected(store, name):
    with pytest.raises(ValueError):
        store.create(name, store.home)


def test_hook_delivery_and_bounded_stop(store):
    store.send("chief", "product", "Change priority")
    result = handle_hook(store, "product", {"hook_event_name": "UserPromptSubmit"})
    assert "Change priority" in result["hookSpecificOutput"]["additionalContext"]
    assert store.session("product")["state"] == "working"
    store.send("chief", "product", "One more thing")
    stopped = handle_hook(
        store, "product", {"hook_event_name": "Stop", "last_assistant_message": "Work done."}
    )
    assert stopped["decision"] == "block"
    store.send("chief", "product", "Later")
    assert handle_hook(store, "product", {"hook_event_name": "Stop", "stop_hook_active": True}) == {}
    assert store.inbox("product", pending_only=True)[0]["body"] == "Later"
    assert store.inbox("chief") == []


def test_explicit_report_prevents_duplicate_fallback(store):
    handle_hook(store, "product", {"hook_event_name": "UserPromptSubmit"})
    store.report("product", "Short summary")
    handle_hook(store, "product", {"hook_event_name": "Stop", "last_assistant_message": "Long answer"})
    assert len(store.inbox("chief")) == 1


def test_turn_end_acknowledgement_does_not_create_a_reply_loop(store):
    handle_hook(store, "product", {"hook_event_name": "UserPromptSubmit"})
    store.ack("product", [store.send("chief", "product", "Please report when finished")])
    handle_hook(store, "product", {
        "hook_event_name": "Stop", "last_assistant_message": "Acknowledged. I will proceed."
    })
    assert store.inbox("chief") == []


def test_permission_and_failure_states(store):
    handle_hook(
        store, "product", {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
    )
    assert store.session("product")["state"] == "permission"
    handle_hook(store, "product", {"hook_event_name": "PostToolUse"})
    assert store.session("product")["state"] == "working"
    handle_hook(store, "product", {"hook_event_name": "StopFailure", "error": "rate_limit"})
    assert store.session("product")["blocker"] == "rate_limit"


def test_agent_cannot_impersonate_or_spawn(store):
    agent = Protocol(store, "product")
    with pytest.raises(ValueError):
        agent.call("spawn", {"name": "rogue", "task": "Bad", "cwd": str(store.home)})
    with pytest.raises(ValueError):
        agent.call("send", {"to": "product", "message": "Spoof", "sender": "chief"})
    with pytest.raises(ValueError):
        agent.call("send", {"to": "product", "message": "Spoof"})
    message = agent.call("send", {"to": "chief", "message": "Need help"})
    assert message["status"] == "queued"
    assert store.inbox("chief")[0]["sender"] == "product"


def test_mcp_wire_protocol(store):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "report", "arguments": {"summary": "Wire test completed"}},
        },
    ]
    result = subprocess.run(
        Tmux(store).command("_mcp", "product"),
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert "spawn" not in [t["name"] for t in responses[1]["result"]["tools"]]
    assert not responses[2]["result"].get("isError")
    assert store.session("product")["summary"] == "Wire test completed"


def test_claude_configuration_is_scoped_and_resumable(store):
    args = prepare(store, "product")
    assert "--session-id" in args
    assert "--dangerously-skip-permissions" not in args
    assert args[-1] == "Build a product plan"
    settings = json.loads((store.home / "sessions/product/settings.json").read_text())
    assert "Stop" in settings["hooks"]
    store.update("product", launched=1)
    resumed = prepare(store, "product")
    assert "--resume" in resumed
    assert "--session-id" not in resumed
    assert resumed[-1] != "Build a product plan"


def test_mcp_validation_returns_tool_error(store):
    result = Protocol(store, "chief").dispatch(
        {
            "id": 1,
            "method": "tools/call",
            "params": {"name": "send", "arguments": {"to": "product", "message": 45}},
        }
    )
    assert result["result"]["isError"]


def test_chief_wait_receives_reports_without_terminal_input(store):
    store.report("product", "Ready for review")
    messages = Protocol(store, "chief").call("await_updates", {})
    assert messages[0]["kind"] == "report"
    assert "Ready for review" in messages[0]["body"]
    with pytest.raises(ValueError):
        Protocol(store, "product").call("await_updates", {})
