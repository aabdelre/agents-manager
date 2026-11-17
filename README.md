# Chief of Staff

A terminal command desk for a persistent Claude chief and independent Claude workstreams.

The chief delegates, asks for updates, and turns progress into decisions. Each workstream
is a real Claude Code session in its own tmux session. The right sidebar keeps your chief
avatar, session menu, and **your** action items visible while you work directly with Claude.

## Open it

```sh
cd ~/Desktop/chief-of-staff
./chief start
```

Or double-click **Start Chief.command**. Sign in to Claude and accept its workspace trust
prompt if requested. You can also use **Demo.command** or `./chief demo` to explore an
isolated sample portfolio without making Claude requests.

Requirements: macOS or Linux, Python 3.11+, tmux, and an authenticated Claude Code CLI.
Dependencies are installed in this project's `.venv`. On a fresh checkout, run `uv sync`.
`./chief doctor` checks your installation. A wide terminal (120+ columns and 35+ rows) works best.

Try telling the chief:

> Create a research workstream in /path/to/project to compare our deployment options.
> Create a planning workstream in /path/to/other-project to draft a release checklist.
> Coordinate them and bring me a concise update with anything that needs my decision.

Normal Claude permissions apply. The chief's local coordination tools are allowed for its
session; Claude still controls approvals for filesystem, shell, and other work. Agents can
pause at permission or trust prompts. Open that workstream to respond.

## Navigation

| Control | Action |
| --- | --- |
| Click a session in the right sidebar | Open that workstream |
| Click **◀ Prev / Next ▶** at the bottom | Cycle through running sessions |
| **Alt+Left / Alt+Right** | Cycle from either pane |
| Click **Chief** | Return to the chief |
| Click the left pane | Type directly into Claude |
| **Esc** in the sidebar | Focus Claude |
| **Ctrl+b**, then **h / l** | Focus the left / right pane |
| **Ctrl+b**, then **d** | Detach; all sessions keep running |
| **+ New** or **Ctrl+n** in the sidebar | Delegate a new workstream |
| **Probe** | Ask this workstream for an update; on chief, ask all |
| **Inbox** | Read this session's queued and unacknowledged messages |
| Check an action item | Mark your action complete |

If your terminal reserves Option/Alt+arrows, use the clickable controls or configure Option
to send Escape/Meta. With multiple terminals attached to the same session, use Alt+arrows;
the sidebar avoids guessing which terminal to switch.

## CLI

```sh
./chief start                              # start / reattach chief
./chief start --cwd /path/to/project       # working directory when first creating chief
./chief spawn research --cwd /path/to/repo --task "Compare storage options"
./chief send research "Prioritize operating cost and migration effort"
./chief probe research                     # or: chief probe all
./chief list
./chief inspect research                   # read actual terminal output
./chief attach research
./chief inbox chief
./chief todo add research "Choose the preferred storage option"
./chief todo list
./chief todo done 1
./chief stop research                      # end process; preserve records
./chief start research                     # restart / resume its conversation
./chief restore                            # restore all sessions after a reboot
```

Add `--detach` to `start`, `restore`, or `demo` to leave the workspace in the background.
Choose a model when creating a session with `--model`. Existing sessions keep their original
working directory and model. Use different workstream names for different assignments.

The tool uses a dedicated tmux server, so it does not change your `~/.tmux.conf` or existing
tmux sessions. Launch `chief attach` from a fresh terminal rather than nesting tmux clients.

## Communication and persistence

Each message has an ID, sender, recipient, type, body, and creation/delivery/acknowledgement
timestamps. SQLite in WAL mode stores sessions, messages, reports, and action items. Multiple
agents can write concurrently. MCP server identities are bound to their workstream; an agent
cannot use the protocol to impersonate chief or spawn more sessions.

The chief has `spawn`, `send`, `probe`, `inspect`, `list_sessions`, `inbox`, `ack`, `report`,
`await_updates`, `add_todo`, and `list_todos` tools. Workers have the reporting and inbox tools.
`await_updates` lets the chief wait up to 30 seconds for incoming reports during coordination.

Messages enter Claude through its SessionStart, UserPromptSubmit, PostToolUse, and Stop hooks.
The Stop hook allows at most one mailbox-triggered continuation, preventing an endless stop
loop. Unacknowledged messages replay when the conversation resumes. Delivery means the
instruction was presented to Claude; acknowledgement means it was understood, **not** that
the work was completed. Reports record outcomes separately.

Workers are instructed to publish a concise summary, next step, blockers, and human actions.
If a worker finishes a turn without reporting, the Stop hook sends a bounded **turn-end
excerpt**, clearly labeled as such. It is not an independently generated summary.

New, unattended workers can wake automatically when idle. Once you open a session, automatic
typing is disabled for that process to preserve any prompt draft you leave there. Its inbox
still arrives during tools and at turn boundaries. When an opened session is idle, type
**“check inbox”** in Claude to process queued instructions. The sidebar instruction field
queues a durable message; it does not submit the native Claude prompt for you.

Closing a terminal preserves processes in tmux. Rebooting ends those processes; `chief restore`
recreates them using recorded Claude conversation IDs and `--resume`. Claude's own stored
history must still be available. This does not preserve an unfinished tool process across
a reboot. Only sessions with a real saved conversation can be resumed.

Default state: `~/.local/share/chief-of-staff`. Override with `CHIEF_HOME` or
`chief --home /path/to/state ...`. Demo state is a separate sibling ending in `-demo`.
Local state contains your task text and summaries; keep it outside repositories you publish.
Concurrent agents share files when given the same working directory. Use separate directories
or Git worktrees when they will edit overlapping files.

## Development and verification

```sh
uv sync
.venv/bin/pytest -q
CHIEF_TEST_TMUX=1 .venv/bin/pytest -q tests/test_tmux.py
.venv/bin/ruff check src tests
```

The default suite covers durable delivery, concurrent writers, acknowledgements, hook
continuations, reports, MCP framing and role checks, and dashboard clicks/forms. The opt-in
integration suite launches real tmux sessions in isolated temporary state, verifies both
panes and message exchange, then cleans up only those test sessions. Demo tests do not verify model judgment or consume model requests.

Modules: `store.py` owns persistence; `tmux.py` owns sessions; `runtime.py` configures Claude
and handles hooks; `protocol.py` exposes MCP tools; `ui.py` renders the command desk;
`cli.py` provides the user commands. The source launcher also handles macOS environments
that mark editable-install `.pth` files hidden.

Integration references: [Claude hooks](https://code.claude.com/docs/en/hooks),
[Claude MCP configuration](https://code.claude.com/docs/en/mcp), and
[Textual testing](https://textual.textualize.io/guide/testing/).
