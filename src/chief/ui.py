"""The right-hand command desk; Claude keeps its native terminal on the left."""

from __future__ import annotations

import os
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, ListItem, ListView, Static, TextArea

from chief.avatar import ChiefAvatar
from chief.store import Store
from chief.tmux import Tmux

COLORS = {
    "idle": "#7de0bb",
    "working": "#eac28e",
    "starting": "#89b4fa",
    "waking": "#89b4fa",
    "permission": "#f48c9c",
    "error": "#f48c9c",
    "exited": "#75849b",
    "stopped": "#75849b",
}


class SessionItem(ListItem):
    """Keep the click destination on the row, independent of the list cursor."""

    def __init__(self, row, current):
        self.session_name = row["name"]
        self.label = Label()
        super().__init__(self.label)
        self.update_session(row, current)

    def update_session(self, row, current):
        active = self.session_name == current
        self.set_class(active, "-active")
        label = Text("◆ " if active else "  ", style="#7de0bb")
        label.append(self.session_name, style="bold #e7eef8" if active else "#b4c5db")
        label.append("\n  ● ", style=COLORS.get(row["state"], "white"))
        label.append(row["state"], style="#91a7c3")
        if row["unread"]:
            label.append(f" · {row['unread']} unread", style="#eac28e")
        self.label.update(label)


class SessionList(ListView):
    def __init__(self, current):
        super().__init__(id="sessions", initial_index=None)
        self.current = current

    def reset_cursor(self):
        self.index = next(
            (index for index, item in enumerate(self.children) if item.session_name == self.current),
            None,
        )

    def on_blur(self):
        self.reset_cursor()


class NewWorkstream(ModalScreen):
    CSS = """
    NewWorkstream { align: center middle; background: #0a101be0; }
    #form { width: 100%; max-width: 64; height: auto; padding: 1 2; background: #172335; border: round #7de0bb; }
    #form Label { margin-top: 1; color: #a9b8ce; }
    #form Input { margin-bottom: 0; }
    #assignment { height: 6; }
    #form Horizontal { height: 3; margin-top: 1; }
    #form Button { width: 1fr; min-width: 9; }
    #form-error { height: auto; color: #f48c9c; }
    """

    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd

    def compose(self):
        with Vertical(id="form"):
            yield Static("[b]NEW WORKSTREAM[/b]\nGive one agent a clear outcome.")
            yield Label("Name")
            yield Input(placeholder="e.g. product-launch", id="name")
            yield Label("Working directory")
            yield Input(value=self.cwd, id="cwd")
            yield Label("Assignment")
            yield TextArea(id="assignment")
            yield Static("", id="form-error")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Delegate", id="delegate", variant="success")

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, "#delegate")
    def delegate(self):
        values = (
            self.query_one("#name", Input).value.strip(),
            self.query_one("#cwd", Input).value.strip(),
            self.query_one("#assignment", TextArea).text.strip(),
        )
        if not all(values):
            self.query_one("#form-error", Static).update("Name, directory, and assignment are required.")
            return
        self.dismiss(values)


class InboxScreen(ModalScreen):
    CSS = """
    InboxScreen { align: center middle; background: #0a101be0; }
    #mail { width: 100%; height: 90%; padding: 1; background: #172335; border: round #7de0bb; }
    #mail-scroll { height: 1fr; }
    #mail Static { height: auto; margin-bottom: 1; }
    """

    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def compose(self):
        with Vertical(id="mail"):
            yield Static(
                "[b]INBOX[/b]\nMessages prompt Claude automatically. Unacknowledged messages remain here."
            )
            with VerticalScroll(id="mail-scroll"):
                if not self.messages:
                    yield Static("All caught up.")
                for message in self.messages:
                    state = "delivered" if message["delivered"] else "queued"
                    yield Static(Text(f"#{message['id']} · {message['sender']} · {state}\n{message['body']}"))
            yield Button("Close", id="close-mail")

    @on(Button.Pressed, "#close-mail")
    def close_mail(self):
        self.dismiss(None)


class Sidebar(App):
    TITLE = "Chief of Staff"
    CSS = """
    Screen { background: #0e1726; color: #d6e2f3; }
    #brand { height: 2; padding: 0 1; color: #88a2c4; text-style: bold; }
    #avatar { height: 8; content-align: center middle; background: #172335; }
    #portfolio { height: 2; content-align: center middle; color: #91a7c3; }
    .eyebrow { height: 2; padding: 1 1 0 1; color: #7de0bb; text-style: bold; }
    #sessions { height: auto; max-height: 10; min-height: 3; background: #0e1726; margin: 0 1; }
    #sessions ListItem { height: 2; padding: 0 1; border-left: solid #0e1726; }
    #sessions ListItem.-highlight { background: #0e1726; text-style: none; }
    #sessions ListItem.-hovered { background: #1b2b3e; }
    #sessions:focus ListItem.-highlight { background: #1b2b3e; text-style: underline; }
    #sessions ListItem.-active, #sessions:focus ListItem.-active {
        background: #253d4b; border-left: solid #7de0bb; text-style: none;
    }
    #summary { height: auto; max-height: 6; padding: 1 2; color: #a9bad1; }
    #todos { height: 1fr; min-height: 3; padding: 0 1; }
    #todos Checkbox { width: 100%; height: auto; min-height: 2; background: #0e1726; border: none; padding: 0 1; }
    #empty-todos { padding: 1; color: #7185a1; }
    .actions { height: 3; padding: 0 1; }
    .actions Button { width: 1fr; min-width: 6; margin: 0; border: none; background: #21334a; }
    .actions Button:hover { background: #2e4964; }
    #new { color: #7de0bb; }
    #message { margin: 1 1 0 1; height: 3; border: tall #2d425b; background: #172335; }
    #hint { height: 2; padding: 0 1; color: #7185a1; }
    #navigation { height: 3; dock: bottom; background: #172335; }
    #navigation Button { width: 1fr; min-width: 7; border: none; background: #172335; color: #7de0bb; }
    """
    BINDINGS: ClassVar = [
        ("ctrl+n", "new", "New workstream"),
        ("escape", "focus_claude", "Claude"),
        ("ctrl+left", "previous", "Previous"),
        ("ctrl+right", "next", "Next"),
    ]

    def __init__(self, store, name):
        super().__init__()
        self.store: Store = store
        self.session_name = name
        self.tmux = Tmux(store)
        self.last_sessions = None
        self.last_todos = None

    def compose(self) -> ComposeResult:
        yield Static("C / S       CHIEF OF STAFF", id="brand")
        yield ChiefAvatar()
        yield Static("Connecting your workstreams…", id="portfolio")
        yield Static("OPEN SESSIONS", classes="eyebrow")
        yield SessionList(self.session_name)
        yield Static("", id="summary")
        yield Static("YOUR NEXT MOVES", classes="eyebrow")
        yield VerticalScroll(id="todos")
        with Horizontal(classes="actions"):
            yield Button("+ New", id="new")
            yield Button("Probe", id="probe")
            yield Button("Inbox", id="inbox")
        yield Input(placeholder=f"Instruction → {self.session_name}", id="message")
        yield Static("Enter sends · Esc returns to Claude", id="hint")
        with Horizontal(id="navigation"):
            yield Button("◀ Prev", id="previous")
            yield Button("Chief", id="chief")
            yield Button("Next ▶", id="next")

    async def on_mount(self):
        await self.refresh_data()
        self.set_interval(1.5, self.refresh_data)

    def on_resize(self, event):
        # tmux's initial client attachment may proportionally shrink the split after
        # its attach hook. Keep the command desk readable once the terminal settles.
        pane = os.environ.get("TMUX_PANE")
        if pane and event.size.width != 42:
            try:
                self.tmux.run("resize-pane", "-t", pane, "-x", "42")
            except (RuntimeError, OSError):
                pass

    async def refresh_data(self):
        rows = self.store.sessions()
        signature = [(r["name"], r["state"], r["unread"]) for r in rows]
        if signature != self.last_sessions:
            listing = self.query_one("#sessions", SessionList)
            items = {item.session_name: item for item in listing.children}
            names = [row["name"] for row in rows]
            membership_changed = list(items) != names
            cursor = listing.highlighted_child
            if membership_changed:
                listing.index = None
                await listing.remove_children([item for name, item in items.items() if name not in names])
            for index, row in enumerate(rows):
                item = items.get(row["name"])
                if item is None:
                    item = SessionItem(row, self.session_name)
                    await listing.append(item)
                else:
                    # Status updates must not unmount a row between mouse-down and mouse-up.
                    item.update_session(row, self.session_name)
                if membership_changed:
                    listing.move_child(item, before=index)
            if membership_changed:
                if cursor is not None and cursor.session_name in names and listing.has_focus:
                    listing.index = names.index(cursor.session_name)
                else:
                    listing.reset_cursor()
            self.last_sessions = signature
        workers = [r for r in rows if r["role"] == "agent"]
        working = sum(r["state"] in {"working", "waking"} for r in workers)
        self.query_one("#portfolio", Static).update(f"{len(workers)} workstreams  ·  {working} working")
        current = self.store.session(self.session_name)
        description = current["blocker"] or current["next_step"] or current["summary"] or current["task"]
        self.query_one("#summary", Static).update(
            Text(description[:230] if description else "Ready for your next move.")
        )
        todos = self.store.todos()
        if todos != self.last_todos:
            area = self.query_one("#todos", VerticalScroll)
            await area.remove_children()
            if not todos:
                await area.mount(
                    Static("Nothing waiting on you.\nYour agents have the next move.", id="empty-todos")
                )
            for todo in todos:
                await area.mount(
                    Checkbox(Text(f"{todo['session']}\n{todo['text']}"), id=f"todo-{todo['id']}")
                )
            self.last_todos = todos

    def guarded(self, action):
        try:
            return action()
        except (ValueError, RuntimeError, OSError) as exc:
            self.notify(str(exc), severity="error", timeout=7)
            return None

    @on(ListView.Selected, "#sessions")
    def select_session(self, event):
        if isinstance(event.item, SessionItem):
            self.navigate(event.item.session_name)

    def navigate(self, target=None, *, direction=1):
        if target is None:
            self.guarded(lambda: self.tmux.navigate(self.session_name, direction=direction))
        else:
            self.guarded(lambda: self.tmux.navigate(self.session_name, target))
        # Every tmux session owns its sidebar. Leave this one pointing at its own
        # session, so returning via a click, arrow, or tmux never shows a stale cursor.
        self.query_one("#sessions", SessionList).reset_cursor()

    @on(Checkbox.Changed)
    def complete_todo(self, event):
        if event.value and event.checkbox.id and event.checkbox.id.startswith("todo-"):
            self.store.complete_todo(int(event.checkbox.id[5:]))
            self.notify("Action completed")

    @on(Input.Submitted, "#message")
    def send(self, event):
        if event.value.strip():
            result = self.guarded(lambda: self.store.send("user", self.session_name, event.value))
            if result:
                event.input.value = ""
                self.notify(f"Message #{result} queued for direct delivery to Claude.", timeout=4)

    @on(Button.Pressed)
    def button(self, event):
        actions = {
            "previous": self.action_previous,
            "next": self.action_next,
            "new": self.action_new,
            "chief": lambda: self.navigate("chief"),
            "probe": self.probe,
            "inbox": self.inbox,
        }
        if event.button.id in actions:
            actions[event.button.id]()

    def action_previous(self):
        self.navigate(direction=-1)

    def action_next(self):
        self.navigate(direction=1)

    def action_focus_claude(self):
        self.guarded(
            lambda: self.tmux.run("select-pane", "-t", self.store.session(self.session_name)["pane"])
        )

    def inbox(self):
        self.push_screen(InboxScreen(self.store.inbox(self.session_name)))

    def probe(self):
        targets = (
            [r["name"] for r in self.store.sessions() if r["role"] == "agent"]
            if self.session_name == "chief"
            else [self.session_name]
        )
        for name in targets:
            self.store.send(
                "user", name, "Send chief a concise progress report with next step and blockers.", "probe"
            )
        self.notify(f"Update requested from {len(targets)} workstream(s).")

    @work
    async def action_new(self):
        values = await self.push_screen_wait(NewWorkstream(self.store.session(self.session_name)["cwd"]))
        if values:
            name, cwd, task = values

            def create():
                self.store.create(name, cwd, task, demo=bool(self.store.session(self.session_name)["demo"]))
                self.tmux.ensure(name)
                self.notify(f"Delegated to {name}")

            self.guarded(create)


class DemoTerminal(App):
    CSS = """
    Screen { background: #0b1220; color: #d6e2f3; padding: 2 3; }
    #masthead { color: #7de0bb; height: 4; }
    #transcript { height: 1fr; }
    .copy { height: auto; margin-bottom: 2; }
    #demo-input { dock: bottom; background: #172335; border: tall #37546b; }
    """

    def __init__(self, store, name):
        super().__init__()
        self.store = store
        self.session_name = name

    def compose(self):
        row = self.store.session(self.session_name)
        yield Static(
            f"[b]CHIEF OF STAFF[/b]   /   {self.session_name}\nDEMO · local simulation · no Claude requests",
            id="masthead",
        )
        with VerticalScroll(id="transcript"):
            yield Static(Text(row["task"] or "Your portfolio, in one place."), classes="copy")
            yield Static(
                Text(
                    row["summary"]
                    or "Ready for a new assignment. Your workstream will stay here when you detach."
                ),
                classes="copy",
            )
            yield Static(
                "[b]A quieter way to run parallel work.[/b]\n\nTalk to the chief. Delegate a clear outcome.\nKeep decisions in view while your agents make progress.\n\nClick a session on the right to switch.\nClick a checkbox to finish a human action.\nUse the arrows below to move between workstreams.\n\n[dim]Ctrl+b, then d detaches. Your sessions keep running.[/dim]",
                classes="copy",
            )
        yield Input(placeholder="Try a message…", id="demo-input")

    def on_mount(self):
        self.store.update(self.session_name, state="idle")
        self.set_interval(1, self.process_mail)

    async def process_mail(self):
        for message in self.store.deliver(self.session_name):
            await self.query_one("#transcript", VerticalScroll).mount(
                Static(Text(f"{message['sender']} → {self.session_name}\n{message['body']}"), classes="copy")
            )
            self.store.ack(self.session_name, [message["id"]])
            if self.session_name != "chief":
                self.store.report(
                    self.session_name,
                    "Demo: instruction received. No actual work was performed.",
                    next_step="Connect a live Claude session to execute this assignment.",
                )

    @on(Input.Submitted)
    async def prompt(self, event):
        if event.value.strip():
            await self.query_one("#transcript", VerticalScroll).mount(
                Static(Text("You\n" + event.value), classes="copy")
            )
            event.input.value = ""
