"""Isolated tmux server: one real Claude pane and one dashboard per session."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from chief.store import Store


class Tmux:
    def __init__(self, store: Store):
        self.store = store
        self.socket = "chief-" + hashlib.sha256(str(store.home).encode()).hexdigest()[:12]

    def command(self, *args):
        return [
            sys.executable,
            str(Path(__file__).with_name("entry.py")),
            "--home",
            str(self.store.home),
            *args,
        ]

    def run(self, *args, check=True):
        result = subprocess.run(
            ["tmux", "-L", self.socket, *args], text=True, capture_output=True, check=False
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or "tmux command failed")
        return result.stdout.strip() if check else result

    def exists(self, name):
        return self.run("has-session", "-t", "=" + name, check=False).returncode == 0

    def configure(self):
        nav = shlex.join(self.command("_navigate"))
        # This file belongs only to our separate server; the user's tmux config is untouched.
        config = self.store.home / "tmux.conf"
        config.write_text(
            "\n".join(
                [
                    "set -g mouse on",
                    "set -g default-terminal tmux-256color",
                    "set -g status-position bottom",
                    "set -g status-style 'bg=#172335,fg=#9cacc4'",
                    "set -g status-left-length 24",
                    "set -g status-right-length 90",
                    "set -g status-left '#[fg=#79dfc0,bold] ◀ PREV │ NEXT ▶ '",
                    "set -g status-right '#[fg=#e8c28c] #S #[fg=#677f9c]│ Alt+←/→ sessions · Ctrl+b d detach '",
                    "set -g window-status-format ''",
                    "set -g window-status-current-format ''",
                    "set -g pane-border-style 'fg=#25354b'",
                    "set -g pane-active-border-style 'fg=#4e8f88'",
                    "set -g history-limit 50000",
                    "set -g exit-empty on",
                    "set -g remain-on-exit on",
                    "set -g allow-rename off",
                    "set -g automatic-rename off",
                    "set-hook -g client-resized 'resize-pane -t \"{right}\" -x 42'",
                    "set-hook -g client-attached 'resize-pane -t \"{right}\" -x 42'",
                    "set-hook -g client-session-changed 'resize-pane -t \"{right}\" -x 42'",
                    f"bind -n M-Left run-shell {shlex.quote(nav + ' prev --client #{client_name} --current #{session_name}')}",
                    f"bind -n M-Right run-shell {shlex.quote(nav + ' next --client #{client_name} --current #{session_name}')}",
                    f"bind -n MouseDown1StatusLeft run-shell {shlex.quote(nav + ' click --client #{client_name} --current #{session_name} --x #{mouse_x}')}",
                    "bind h select-pane -L",
                    "bind l select-pane -R",
                ]
            )
            + "\n"
        )
        self.run("source-file", str(config))

    def ensure(self, name):
        with (self.store.home / "launch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._ensure(name)

    def _ensure(self, name):
        if not shutil.which("tmux"):
            raise RuntimeError("tmux is required. Install it with: brew install tmux")
        row = self.store.session(name)
        if not row["demo"] and not shutil.which("claude"):
            raise RuntimeError("Claude Code is required. Install it and run `claude` to sign in first.")
        if self.exists(name):
            self.configure()
            dead = self.run("display-message", "-p", "-t", row["pane"], "#{pane_dead}")
            if dead == "1":
                self.store.update(name, state="starting", attended=0)
                self.run(
                    "respawn-pane",
                    "-t",
                    row["pane"],
                    "-c",
                    row["cwd"],
                    shlex.join(self.command("_run", name)),
                )
            sidebar_dead = self.run("display-message", "-p", "-t", row["sidebar"], "#{pane_dead}")
            if sidebar_dead == "1":
                self.run("respawn-pane", "-t", row["sidebar"], shlex.join(self.command("_sidebar", name)))
            return
        pane = self.run(
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            name,
            "-n",
            "desk",
            "-x",
            "160",
            "-y",
            "45",
            "-c",
            row["cwd"],
            shlex.join(self.command("_run", name)),
        )
        self.store.update(name, pane=pane, state="starting", attended=0)
        self.configure()
        side = self.run(
            "split-window",
            "-h",
            "-l",
            "42",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            pane,
            "-c",
            str(self.store.home),
            shlex.join(self.command("_sidebar", name)),
        )
        self.store.update(name, sidebar=side)
        self.run("select-pane", "-t", pane)

    def attached(self, name):
        result = self.run("list-clients", "-t", name, "-F", "#{client_name}", check=False)
        return result.stdout.strip().splitlines() if result.returncode == 0 else []

    def attach(self, name):
        self.ensure(name)
        self.store.update(name, attended=1)
        if os.environ.get("TMUX"):
            # A nested client would trap shortcuts and mix servers; a fresh terminal is explicit.
            raise ValueError(
                "Already inside tmux. Use the sidebar here, or run `chief attach` in a fresh terminal."
            )
        os.execvp("tmux", ["tmux", "-L", self.socket, "attach-session", "-t", name])

    def navigate(self, current, target=None, direction=1, client=None):
        sessions = [r["name"] for r in self.store.sessions() if self.exists(r["name"])]
        if not sessions:
            raise ValueError("No running sessions. Run `chief start`.")
        if target is None:
            index = sessions.index(current) if current in sessions else 0
            target = sessions[(index + direction) % len(sessions)]
        if target not in sessions:
            raise ValueError(f"Session is not running: {target}. Use `chief start {target}` to restore it.")
        clients = [client] if client else self.attached(current)
        if len(clients) > 1:
            raise ValueError(
                "Multiple terminals are viewing this session. Use Alt+Left/Right in your terminal."
            )
        self.store.update(current, attended=1)
        self.store.update(target, attended=1)
        for destination in clients:
            self.run("switch-client", "-c", destination, "-t", target)
        return target

    def capture(self, name, lines=80):
        row = self.store.session(name)
        if not self.exists(name):
            return "Session is offline. Its messages and summary are preserved."
        return self.run("capture-pane", "-p", "-J", "-t", row["pane"], "-S", str(-min(lines, 300)))

    def wake_if_safe(self, name):
        row = self.store.session(name)
        if row["state"] != "idle" or row["attended"] or not self.store.inbox(name, pending_only=True):
            return False
        if self.attached(name):
            self.store.update(name, attended=1)
            return False
        self.store.update(name, state="waking")
        self.run(
            "send-keys",
            "-t",
            row["pane"],
            "-l",
            "Read your Chief inbox, acknowledge messages, and act on them.",
        )
        # Separate commands let Claude's TTY read the text before the submit key.
        import time

        time.sleep(0.15)
        self.run("send-keys", "-t", row["pane"], "Enter")
        return True

    def stop(self, name):
        if self.exists(name):
            self.run("kill-session", "-t", "=" + name)
        self.store.update(name, state="stopped")
