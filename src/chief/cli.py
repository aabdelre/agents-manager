from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from chief.store import Store, default_home
from chief.tmux import Tmux


def parser():
    p = argparse.ArgumentParser(
        prog="chief", description="A chief of staff and persistent Claude workstreams in your terminal."
    )
    p.add_argument(
        "--home", type=Path, default=default_home(), help="Isolated state directory (or CHIEF_HOME)"
    )
    p.add_argument("--version", action="version", version="chief 0.1.0")
    commands = p.add_subparsers(dest="command")
    start = commands.add_parser("start", help="Create or restore a session, then open the command desk")
    start.add_argument("name", nargs="?", default="chief")
    start.add_argument("--cwd", default=os.getcwd())
    start.add_argument("--model")
    start.add_argument("--detach", action="store_true")
    spawn = commands.add_parser("spawn", help="Delegate work to a new Claude session")
    spawn.add_argument("name")
    spawn.add_argument("--task", required=True)
    spawn.add_argument("--cwd", default=os.getcwd())
    spawn.add_argument("--model")
    attach = commands.add_parser("attach", help="Reattach to a preserved session")
    attach.add_argument("name", nargs="?", default="chief")
    restore = commands.add_parser(
        "restore", help="Restore all recorded sessions after reboot or tmux shutdown"
    )
    restore.add_argument("--detach", action="store_true")
    restart = commands.add_parser("restart", help="Reload an idle session with its saved Claude conversation")
    restart.add_argument("name", nargs="?", default="chief")
    listing = commands.add_parser("list", help="List status and workstream summaries")
    listing.add_argument("--json", action="store_true")
    send = commands.add_parser("send", help="Queue an instruction for a session")
    send.add_argument("to")
    send.add_argument("message")
    probe = commands.add_parser("probe", help="Request concise workstream updates")
    probe.add_argument("name", nargs="?", default="all")
    inbox = commands.add_parser("inbox", help="Read unacknowledged messages")
    inbox.add_argument("name", nargs="?", default=os.environ.get("CHIEF_SESSION", "chief"))
    inbox.add_argument("--all", action="store_true", help="Include acknowledged messages")
    ack = commands.add_parser("ack", help="Acknowledge inbox messages")
    ack.add_argument("ids", type=int, nargs="+")
    ack.add_argument("--session", default=os.environ.get("CHIEF_SESSION", "chief"))
    report = commands.add_parser("report", help="Publish a concise workstream report")
    report.add_argument("--session", default=os.environ.get("CHIEF_SESSION", "chief"))
    report.add_argument("--summary", required=True)
    report.add_argument("--next", dest="next_step", default="")
    report.add_argument("--blocker", default="")
    report.add_argument("--todo", action="append", default=[])
    todo = commands.add_parser("todo", help="Manage human action items").add_subparsers(
        dest="todo_command", required=True
    )
    add = todo.add_parser("add")
    add.add_argument("session")
    add.add_argument("text")
    done = todo.add_parser("done")
    done.add_argument("id", type=int)
    todo.add_parser("list")
    inspect = commands.add_parser("inspect", help="Read a workstream's actual terminal")
    inspect.add_argument("name")
    stop = commands.add_parser(
        "stop", help="End a session process; keep its conversation and records for resume"
    )
    stop.add_argument("name")
    commands.add_parser("doctor", help="Check dependencies and Claude authentication")
    demo = commands.add_parser(
        "demo", help="Open an isolated sample portfolio without making Claude requests"
    )
    demo.add_argument("--detach", action="store_true")
    for internal in ("_run", "_sidebar", "_hook", "_mcp"):
        internal_parser = commands.add_parser(internal, help=argparse.SUPPRESS)
        internal_parser.add_argument("name")
        if internal in ("_hook", "_mcp"):
            internal_parser.add_argument("--channel", action="store_true", help=argparse.SUPPRESS)
    nav = commands.add_parser("_navigate", help=argparse.SUPPRESS)
    nav.add_argument("direction", choices=["prev", "next", "click"])
    nav.add_argument("--client")
    nav.add_argument("--current", required=True)
    nav.add_argument("--x", type=int, default=0)
    return p


def seed_demo(store):
    samples = [
        (
            "chief",
            "Keep the portfolio moving. Surface decisions; delegate the details.",
            "Three workstreams are moving. Two decisions need you today.\n\nProduct is ready for scope review. Research has a shortlist. Launch is waiting on your choice of date.",
            "Review the two decisions on your right.",
        ),
        (
            "product",
            "Shape the next release into a small, shippable plan.",
            "Release plan drafted. Five milestones, clear owners, and a smaller first delivery.",
            "Confirm the first-release scope.",
        ),
        (
            "research",
            "Compare the options and prepare an evidence-backed recommendation.",
            "Shortlist complete. Three options compared across cost, effort, and implementation risk.",
            "Pressure-test the recommendation.",
        ),
        (
            "launch",
            "Coordinate launch readiness and identify dependencies.",
            "Readiness checklist is ready. Messaging and rollout plan depend on the launch date.",
            "Choose the launch window.",
        ),
    ]
    for name, task, summary, next_step in samples:
        if name not in [r["name"] for r in store.sessions()]:
            store.create(name, Path.cwd(), task, demo=True)
            store.update(name, summary=summary, next_step=next_step)
    if not store.todos(all_items=True):
        store.add_todo("product", "Approve the first-release scope")
        store.add_todo("launch", "Choose the launch window")


def execute(args):
    if args.command == "doctor":
        checks = {name: shutil.which(name) for name in ("claude", "tmux")}
        for name, path in checks.items():
            print(f"{'OK' if path else 'MISSING'}  {name}: {path or 'not on PATH'}")
        print(f"State: {args.home}")
        if checks["claude"]:
            result = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, check=False)
            try:
                status = json.loads(result.stdout)
                print(f"Claude sign-in: {'ready' if status.get('loggedIn') else 'run claude to sign in'}")
            except json.JSONDecodeError:
                print("Claude sign-in: check with `claude auth status`")
        return 0 if all(checks.values()) else 1
    if args.command == "demo":
        # A sibling state directory prevents demo data from mixing with real conversations.
        args.home = args.home.with_name(args.home.name + "-demo")
    store = Store(args.home)
    tmux = Tmux(store)
    command = args.command or "start"
    if command == "start":
        name = getattr(args, "name", "chief")
        if name not in [r["name"] for r in store.sessions()]:
            if name != "chief":
                raise ValueError("Create a workstream with `chief spawn NAME --task '…'`.")
            store.create(name, getattr(args, "cwd", os.getcwd()), model=getattr(args, "model", None))
        tmux.ensure(name)
        if not getattr(args, "detach", False):
            tmux.attach(name)
        else:
            print(f"{name} is running. Open it with: chief --home {store.home} attach {name}")
    elif command == "spawn":
        chief = store.session("chief")
        store.create(args.name, args.cwd, args.task, args.model, demo=bool(chief["demo"]))
        tmux.ensure(args.name)
        print(f"Delegated to {args.name}. Open it with: chief attach {args.name}")
    elif command == "attach":
        tmux.attach(args.name)
    elif command == "restore":
        for row in store.sessions():
            tmux.ensure(row["name"])
        if not args.detach:
            tmux.attach("chief")
    elif command == "restart":
        tmux.restart(args.name)
        print(f"Restarting {args.name} with its saved Claude conversation.")
    elif command == "demo":
        seed_demo(store)
        for row in store.sessions():
            tmux.ensure(row["name"])
        if not args.detach:
            tmux.attach("chief")
        else:
            print(f"Demo ready. Attach with: chief --home {store.home} attach")
    elif command == "list":
        rows = store.sessions()
        for row in rows:
            if not tmux.exists(row["name"]):
                row["state"] = "offline"
        if args.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print("No workstreams yet. Run `chief start`.")
        else:
            for row in rows:
                print(f"{row['name']:<20} {row['state']:<12} {row['unread']} unacknowledged")
                if row["summary"]:
                    print("  " + row["summary"].replace("\n", " ")[:140])
    elif command == "send":
        print(f"Queued message #{store.send('user', args.to, args.message)} for {args.to}.")
    elif command == "probe":
        names = (
            [r["name"] for r in store.sessions() if r["role"] == "agent"]
            if args.name == "all"
            else [args.name]
        )
        for name in names:
            store.send(
                "user", name, "Report concise progress, evidence, next step, and blockers to chief.", "probe"
            )
        print(f"Requested updates from {len(names)} workstream(s).")
    elif command == "inbox":
        print(json.dumps(store.inbox(args.name, include_acked=args.all), indent=2))
    elif command == "ack":
        store.ack(args.session, args.ids)
        print("Acknowledged.")
    elif command == "report":
        store.report(args.session, args.summary, args.next_step, args.blocker, args.todo)
        print("Report sent to chief.")
    elif command == "todo":
        if args.todo_command == "add":
            store.add_todo(args.session, args.text)
        elif args.todo_command == "done":
            store.complete_todo(args.id)
        else:
            for row in store.todos():
                print(f"{row['id']:>3}  {row['session']:<16} {row['text']}")
    elif command == "inspect":
        print(tmux.capture(args.name))
    elif command == "stop":
        tmux.stop(args.name)
        print(f"Stopped {args.name}. Records preserved; restore with `chief start {args.name}`.")
    elif command == "_navigate":
        direction = -1 if args.direction == "prev" or (args.direction == "click" and args.x < 8) else 1
        tmux.navigate(args.current, direction=direction, client=args.client)
    elif command == "_sidebar":
        from chief.ui import Sidebar

        Sidebar(store, args.name).run()
    elif command == "_run":
        from chief.runtime import run_session

        run_session(store, args.name)
    elif command == "_hook":
        from chief.runtime import handle_hook

        try:
            payload = json.load(sys.stdin)
            result = handle_hook(store, args.name, payload, channel=args.channel)
            print(json.dumps(result))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"Chief hook: {exc}", file=sys.stderr)
            return 1
    elif command == "_mcp":
        from chief.protocol import Protocol

        Protocol(store, args.name, channel=args.channel).serve()
    return 0


def main():
    try:
        raise SystemExit(execute(parser().parse_args()))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"chief: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        raise SystemExit(130)
