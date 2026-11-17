from unittest.mock import Mock

from textual.widgets import Input, ListView, TextArea

from chief.ui import NewWorkstream, Sidebar


async def test_sidebar_actions_and_navigation(store):
    store.add_todo("product", "Approve release")
    app = Sidebar(store, "chief")
    app.tmux = Mock()
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.pause()
        assert len(app.query_one("#sessions", ListView).children) == 2
        listing = app.query_one("#sessions", ListView)
        listing.focus()
        listing.index = 1
        await pilot.press("enter")
        app.tmux.navigate.assert_called_with("chief", "product")
        await pilot.click("#next")
        app.tmux.navigate.assert_called_with("chief", direction=1)
        await pilot.click("#previous")
        app.tmux.navigate.assert_called_with("chief", direction=-1)
        await pilot.click("#probe")
        assert store.inbox("product")[0]["kind"] == "probe"
        await pilot.click("#todo-1")
        assert not store.todos()
        field = app.query_one("#message", Input)
        field.value = "Reprioritize the launch"
        field.focus()
        await pilot.press("enter")
        assert store.inbox("chief")[0]["body"] == "Reprioritize the launch"
        await pilot.click("#inbox")
        await pilot.pause()
        await pilot.click("#close-mail")


async def test_new_workstream_form(store):
    app = Sidebar(store, "chief")
    app.tmux = Mock()
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.click("#new")
        await pilot.pause()
        assert isinstance(app.screen, NewWorkstream)
        app.screen.query_one("#name", Input).value = "launch"
        app.screen.query_one("#assignment", TextArea).text = "Draft a launch checklist"
        await pilot.click("#delegate")
        await pilot.pause()
        assert store.session("launch")["task"] == "Draft a launch checklist"
        app.tmux.ensure.assert_called_with("launch")


async def test_sidebar_small_terminal(store):
    app = Sidebar(store, "chief")
    async with app.run_test(size=(36, 28)) as pilot:
        await pilot.pause()
        assert app.query_one("#next").region.bottom <= 28
