import fcntl
import os
import select
import struct
import subprocess
import termios
import time

import pytest

from chief.store import Store
from chief.tmux import Tmux


@pytest.mark.skipif(
    os.environ.get("CHIEF_TEST_TMUX") != "1", reason="Set CHIEF_TEST_TMUX=1 for real tmux integration"
)
def test_real_tmux_sessions_persist_and_exchange_messages(tmp_path):
    store = Store(tmp_path / "tmux-test")
    store.create("chief", tmp_path, demo=True)
    store.create("research", tmp_path, "Find options", demo=True)
    tmux = Tmux(store)
    try:
        tmux.ensure("chief")
        tmux.ensure("research")
        tmux.ensure("chief")
        assert tmux.exists("chief") and tmux.exists("research")
        assert len(tmux.run("list-panes", "-t", "chief").splitlines()) == 2
        store.send("chief", "research", "Prepare an update")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not store.session("research")["summary"]:
            time.sleep(0.2)
        assert "Demo:" in store.session("research")["summary"]
        assert "DEMO" in tmux.capture("chief")
        assert tmux.navigate("chief", direction=1) == "research"
        assert tmux.navigate("chief", direction=-1) == "research"
        sidebar = tmux.run("capture-pane", "-p", "-t", store.session("chief")["sidebar"])
        assert "CHIEF OF STAFF" in sidebar
        assert "OPEN SESSIONS" in sidebar
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        client = subprocess.Popen(
            ["tmux", "-L", tmux.socket, "attach", "-t", "chief"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**os.environ, "TERM": "xterm-256color"},
        )

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    os.read(master, 65536)
                if predicate():
                    return
            pytest.fail("Attached tmux client did not navigate as expected")

        try:
            wait_for(lambda: bool(tmux.attached("chief")))
            client_name = tmux.attached("chief")[0]
            tmux.navigate("chief", target="research", client=client_name)
            wait_for(lambda: bool(tmux.attached("research")))
            wait_for(
                lambda: (
                    tmux.run(
                        "display-message", "-p", "-t", store.session("research")["sidebar"], "#{pane_width}"
                    )
                    == "42"
                )
            )
            # Click the actual tmux status-bar previous arrow, then use Alt+Right.
            os.write(master, b"\x1b[<0;4;40M\x1b[<0;4;40m")
            wait_for(lambda: bool(tmux.attached("chief")))
            os.write(master, b"\x1b[1;3C")
            wait_for(lambda: bool(tmux.attached("research")))
        finally:
            os.close(master)
            os.close(slave)
            client.terminate()
            try:
                client.wait(timeout=2)
            except subprocess.TimeoutExpired:
                client.kill()
                client.wait(timeout=2)
        tmux.stop("research")
        assert not tmux.exists("research")
        assert store.session("research")["summary"]
        tmux.ensure("research")
        assert tmux.exists("research")
    finally:
        for row in store.sessions():
            if tmux.exists(row["name"]):
                tmux.stop(row["name"])


def test_safe_wake_does_not_touch_user_drafts(store, monkeypatch):
    tmux = Tmux(store)
    store.update("product", state="idle", attended=1)
    store.send("chief", "product", "New task")
    monkeypatch.setattr(
        tmux, "run", lambda *args, **kwargs: pytest.fail("Must not type into a visited session")
    )
    assert tmux.wake_if_safe("product") is False
