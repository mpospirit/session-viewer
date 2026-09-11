# session-viewer

A local, read-only web UI that lists your coding-harness sessions (Claude Code,
Pi, Cursor, Antigravity) across all projects, lets you browse their
transcripts, and gives you a ready-to-run command to resume any of them.

Single-file, standard-library-only Python (targets 3.9, the interpreter
shipped with the macOS Command Line Tools). No dependencies to install.

## Run it

```
python3 viewer.py
```

Open http://127.0.0.1:8765

Change the port with `SESSION_VIEWER_PORT`:

```
SESSION_VIEWER_PORT=9000 python3 viewer.py
```

## Install as a background service (macOS)

```
./install.sh
```

This copies `viewer.py` to `~/.local/share/session-viewer` and installs a
LaunchAgent (`local.session-viewer`) that starts it on login and keeps it
running. It has to run from `~/.local/share` rather than this directory
because a LaunchAgent doesn't inherit the TCC grants a terminal session has,
so it can't read `~/Documents`, `~/Desktop`, or `~/Downloads`.

Logs: `~/Library/Logs/session-viewer.log`

To remove everything the installer created:

```
./uninstall.sh
```

## What it does

- Scans for sessions from:
  - Claude Code — `~/.claude/projects`
  - Pi — `~/.pi/agent/sessions`
  - Cursor — `~/.cursor/projects` and `~/.cursor/chats`
  - Antigravity — `~/.gemini/antigravity-cli`
- Shows title, project path, branch, turn count, and whether a session is
  "live" (touched in the last 90 seconds).
- Filters the list from the search bar in the top bar (`/` or `Cmd-K` to
  focus, `Esc` to clear). It matches the title, project path, branch, source,
  and session id, highlights the hits, and the per-source counts follow the
  query.
- Lets you open a session and read its transcript (capped at 600 turns /
  4000 chars per block, so one huge session can't wedge the page).
- Gives you the exact shell command to resume a session (e.g.
  `claude --resume <id>`) — shown in the UI to copy and run yourself; the
  server never executes anything.

## Security model

- Binds to `127.0.0.1` only.
- Every request is checked against `Host: 127.0.0.1:<port>` /
  `localhost:<port>` to block DNS-rebinding.
- API routes require an `X-Session-Viewer-Token` header. The token is
  generated on first run, stored at `~/.local/state/session-viewer/token`
  (mode `600`), and auto-injected into the HTML page served at `/`.
- Entirely read-only against your session files on disk.
