from unittest.mock import Mock

import pytest
from textual.events import Click
from textual.widgets import Input, ListView, TextArea

from chief.ui import NewWorkstream, SessionItem, SessionList, Sidebar


def assert_active_session(app, name):
    active = list(app.query("#sessions .-active"))
    assert len(active) == 1
    assert active[0].session_name == name
    assert app.query_one("#sessions", ListView).highlighted_child is active[0]


@pytest.mark.parametrize("current,target", [("chief", "product"), ("product", "chief")])
async def test_one_click_navigation_preserves_each_sidebars_active_session(store, current, target):
    app = Sidebar(store, current)
    app.tmux = Mock()
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.pause()
        assert_active_session(app, current)
        item = next(item for item in app.query(SessionItem) if item.session_name == target)
        # A single click from outside the list must navigate, including its blank area.
        app.query_one("#message").focus()
        await pilot.click(item, offset=(25, 1))
        app.tmux.navigate.assert_called_once_with(current, target)
        assert_active_session(app, current)
        # Returning to this tmux sidebar must still show its own session, even
        # when no status/unread changes have triggered a refresh.
        await app.refresh_data()
        assert_active_session(app, current)


async def test_click_uses_event_item_even_if_cursor_has_moved(store):
    app = Sidebar(store, "chief")
    app.tmux = Mock()
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.pause()
        listing = app.query_one(SessionList)
        event = ListView.Selected(listing, listing.children[1], 1)
        listing.index = 0
        app.select_session(event)
        app.tmux.navigate.assert_called_once_with("chief", "product")
        assert_active_session(app, "chief")


async def test_status_refresh_during_click_keeps_destination_and_cursor(store):
    app = Sidebar(store, "product")
    app.tmux = Mock()
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.pause()
        listing = app.query_one(SessionList)
        chief, product = listing.children
        listing.focus()
        listing.index = 0
        await pilot.mouse_down(chief, offset=(4, 0))
        store.update("product", state="working")
        store.send("chief", "product", "An update during a click")
        store.create("research", store.home)
        await app.refresh_data()
        assert list(listing.children)[:2] == [chief, product]
        assert listing.highlighted_child is chief
        assert "1 unread" in product.label.render().plain
        assert list(app.query("#sessions .-active")) == [product]
        await pilot.mouse_up(chief, offset=(4, 0))
        # Pilot sends Click separately; unlike a terminal driver, mouse_up alone
        # deliberately doesn't synthesize it.
        await pilot._post_mouse_events([Click], chief, offset=(4, 0), button=1)
        await pilot.pause()
        app.tmux.navigate.assert_called_once_with("product", "chief")
        assert_active_session(app, "product")


async def test_failed_navigation_keeps_current_session(store):
    app = Sidebar(store, "chief")
    app.tmux = Mock()
    app.tmux.navigate.side_effect = ValueError("Session is not running: product")
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.pause()
        await pilot.click(app.query_one(SessionList).children[1])
        app.tmux.navigate.assert_called_once_with("chief", "product")
        assert_active_session(app, "chief")
        assert app.session_name == "chief"
        assert app.query_one("#message", Input).placeholder == "Instruction → chief"


async def test_keyboard_cursor_is_distinct_from_active_session_and_resets_on_blur(store):
    app = Sidebar(store, "product")
    app.tmux = Mock()
    async with app.run_test(size=(42, 44)) as pilot:
        await pilot.pause()
        listing = app.query_one(SessionList)
        listing.focus()
        await pilot.press("up")
        assert listing.highlighted_child.session_name == "chief"
        assert app.query_one("#sessions .-active", SessionItem).session_name == "product"
        app.tmux.navigate.assert_not_called()
        await pilot.click("#message")
        assert_active_session(app, "product")


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
