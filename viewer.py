#!/usr/bin/env python3
"""
Local Session Viewer — lists coding-harness sessions from disk and resumes them.

Read-only against session files. Binds to 127.0.0.1 only. Standard library only,
targets Python 3.9 (the interpreter shipped with the macOS Command Line Tools).

    python3 viewer.py            # http://127.0.0.1:8765

See README.md for the launchd deployment.
"""

import base64
import hashlib
import http.server
import json
import os
import re
import shlex
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.parse

# ---------------------------------------------------------------- config

HOST = "127.0.0.1"
PORT = int(os.environ.get("SESSION_VIEWER_PORT", "8765"))

# A session counts as live if its file was touched inside this window.
LIVE_WINDOW = 90.0

# Guardrails on transcript responses so one enormous session cannot wedge the page.
MAX_TURNS = 600
MAX_TEXT = 4000

HOME = os.path.expanduser("~")
CLAUDE_ROOT = os.path.join(HOME, ".claude", "projects")
PI_ROOT = os.path.join(HOME, ".pi", "agent", "sessions")
CURSOR_TRANSCRIPTS = os.path.join(HOME, ".cursor", "projects")
CURSOR_CHATS = os.path.join(HOME, ".cursor", "chats")
AG_ROOT = os.path.join(HOME, ".gemini", "antigravity-cli")
AG_BRAIN = os.path.join(AG_ROOT, "brain")
AG_SUMMARIES = os.path.join(AG_ROOT, "conversation_summaries.db")
AG_HISTORY = os.path.join(AG_ROOT, "history.jsonl")

STATE_DIR = os.path.join(HOME, ".local", "state", "session-viewer")
TOKEN_PATH = os.path.join(STATE_DIR, "token")

# Cursor ids are not uuids. This must stay permissive enough for them and strict
# enough that nothing here can ever become a path component or a shell word.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

SOURCES = ("claude", "pi", "cursor", "antigravity")

RESUME_ARGV = {
    # claude --resume <id>            verified against claude --help
    "claude": lambda sid: ["claude", "--resume", sid],
    # pi --session <id>               verified: --resume is an interactive picker
    #                                 that takes no argument; --session resolves an id
    "pi": lambda sid: ["pi", "--session", sid],
    # agent --resume="<id>"           verified end to end; the CLI binary is
    #                                  "agent", not "cursor-agent"
    "cursor": lambda sid: ["agent", "--resume=" + sid],
    # agy --conversation <id>          verified end to end on this machine;
    #                                  the CLI binary is "agy", not "antigravity"
    "antigravity": lambda sid: ["agy", "--conversation", sid],
}


def resume_command(src, cwd, sid):
    """The exact shell string shown in the UI, copied, and executed. One source."""
    argv = RESUME_ARGV[src](sid)
    run = " ".join(shlex.quote(a) for a in argv)
    # A session whose workspace never got recorded still resumes, just not from
    # a known directory. Better that than `cd '' &&`.
    return "cd " + shlex.quote(cwd) + " && " + run if cwd else run


# ---------------------------------------------------------------- icons

# Each source's own logo, inlined so the deployed copy stays a single file the
# way install.sh assumes. These are img/<name>.png scaled to 48px, which is 2x
# the largest size the page draws them at:
#
#     sips -Z 48 img/claude.png --out /tmp/claude.png
#
# then base64 of the result. Regenerate the pair together if an icon changes.
ICONS = {
    "claude":
        "data:image/png;base64,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",
    "pi":
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAABGdBTUEAALGPC/xhBQAAACBjSFJNAAB6JgAAgIQAAPoAAACA6AAAdTAAAOpgAAA6mAAAF3CculE8AAAAUGVYSWZNTQAqAAAACAACARIAAwAAAAEAAQAAh2kABAAAAAEAAAAmAAAAAAADoAEAAwAAAAEAAQAAoAIABAAAAAEAAAAwoAMABAAAAAEAAAAwAAAAAMnqQhIAAAIyaVRYdFhNTDpjb20uYWRvYmUueG1wAAAAAAA8eDp4bXBtZXRhIHhtbG5zOng9ImFkb2JlOm5zOm1ldGEvIiB4OnhtcHRrPSJYTVAgQ29yZSA2LjAuMCI+CiAgIDxyZGY6UkRGIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyI+CiAgICAgIDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiCiAgICAgICAgICAgIHhtbG5zOmV4aWY9Imh0dHA6Ly9ucy5hZG9iZS5jb20vZXhpZi8xLjAvIgogICAgICAgICAgICB4bWxuczp0aWZmPSJodHRwOi8vbnMuYWRvYmUuY29tL3RpZmYvMS4wLyI+CiAgICAgICAgIDxleGlmOlBpeGVsWURpbWVuc2lvbj4zMjA8L2V4aWY6UGl4ZWxZRGltZW5zaW9uPgogICAgICAgICA8ZXhpZjpQaXhlbFhEaW1lbnNpb24+MzIwPC9leGlmOlBpeGVsWERpbWVuc2lvbj4KICAgICAgICAgPGV4aWY6Q29sb3JTcGFjZT4xPC9leGlmOkNvbG9yU3BhY2U+CiAgICAgICAgIDx0aWZmOk9yaWVudGF0aW9uPjE8L3RpZmY6T3JpZW50YXRpb24+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9uPgogICA8L3JkZjpSREY+CjwveDp4bXBtZXRhPgqvlLm6AAAB20lEQVRoBe1YS07EMAydIj4rFiA07LgAC8Qp2MAFuBzH4AawQ2LBDiQ+6wHEhs8A5T0xaKYeJ21StyBhS1Ybx7HzHrGbYTBwcQacAWfAGXAGnIHfY6BomrosyyP4bkPLpmsifsx7Bz0siuI14lc7tVjrMXXYwevudNj6bRMRFtpGSQkwbptMrH8T46xhCoCsBF0vcgBdM1wXP6WIY7HYST4VB3abFWjjbqfEiJosALwgwz70Rsm0DtsxlM9OxAIAvwtX6OfXcof4dqzB9iHtlmOrIg4REbKbYbACYLah1EAOYMKY1oE4RXuIJNpb14fFGWWL3EDBPuEp2yWLeATVAD7APsQ67TI3RlN4xHytWABgn2er1NjkJg+gfErZguEUyvWzQhLOoHuzxtC7BQAmDPV5HpMR2ORfoSJgfhWGIXS5MvE9CMWbcw2dzznHTENuDWhHTt1C1wDUpJZGB1DDpkWNRVNYJOBd6Baq/WK7h13rTtFNpUxaAPi5jV4isfwOEJzW51P2GPW1AMAEz2iVBNK7WBWxZL43IFYAetuwTPSvACxJ9JMxrwI5R4hrtGsEw4Zyca4iKUV8jpXvUPmvRXaZnALmmhOodpm7gN3FGXAGnAFnwBlwBv48A1+vd0XLotDvsQAAAABJRU5ErkJggg==",
    "cursor":
        "data:image/png;base64,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",
    "antigravity":
        "data:image/png;base64,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",
}


# ---------------------------------------------------------------- token

def load_token():
    """Read the token, creating it on first run. Surviving restarts keeps open tabs working."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
    except OSError as exc:
        sys.exit("cannot create %s: %s" % (STATE_DIR, exc))

    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as fh:
            tok = fh.read().strip()
        if tok:
            os.chmod(TOKEN_PATH, 0o600)
            return tok

    tok = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok + "\n")
    return tok


TOKEN = ""


# ---------------------------------------------------------------- text helpers

# Slash-command scaffolding and harness chatter that pollutes the first user message.
_STRIP_TAGS = re.compile(
    r"<(command-message|command-args|command-name|local-command-caveat|"
    r"local-command-stdout|system-reminder|user_query|"
    r"ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*?</\1>",
    re.S,
)
_ANY_TAG = re.compile(r"</?[a-zA-Z][\w-]*>")
_WS = re.compile(r"\s+")


def clean_title(text):
    if not text:
        return ""
    cmd = re.search(r"<command-name>\s*(/[\w:-]+)", text)
    stripped = _ANY_TAG.sub(" ", _STRIP_TAGS.sub(" ", text))
    stripped = _WS.sub(" ", stripped).strip()
    if cmd and (not stripped or len(stripped) < 3):
        return cmd.group(1)
    if stripped.startswith("This session is being continued from a previous"):
        return "continued session"
    if cmd and stripped.startswith(cmd.group(1)):
        return _WS.sub(" ", stripped)[:200]
    return stripped[:200]


# Which input field best describes a tool call, in preference order.
_TOOL_ARG_KEYS = ("command", "file_path", "path", "pattern", "url", "query",
                  "skill", "prompt", "description", "cmd")


def tool_arg(inp):
    if not isinstance(inp, dict):
        return _WS.sub(" ", str(inp))[:200]
    for k in _TOOL_ARG_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return _WS.sub(" ", v.strip())[:200]
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return _WS.sub(" ", v.strip())[:200]
    return ""


def iter_json_lines(path):
    """Yield parsed objects. A line still being written is skipped, not fatal."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


# ---------------------------------------------------------------- claude code

def decode_project_dir(name):
    """-Users-me-dev-repo  ->  /Users/me/dev/repo. Only a fallback; cwd in the
    records is authoritative because this mapping is lossy for dashed names."""
    return "/" + name.lstrip("-").replace("-", "/") if name.startswith("-") else name


def claude_turn(d):
    """A user/assistant record as (who, blocks), or None if it renders to nothing.
    One predicate so the turn count in the list and the transcript never disagree."""
    if d.get("type") not in ("user", "assistant") or d.get("isSidechain"):
        return None
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    who = "you" if d.get("type") == "user" else "agent"
    content = msg.get("content")
    blocks = []
    if isinstance(content, str):
        txt = clean_title(content) if who == "you" else content.strip()
        if txt:
            blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text") or ""
                txt = clean_title(txt) if who == "you" else txt.strip()
                if txt:
                    blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
            elif bt == "tool_use":
                blocks.append({"kind": "tool", "verb": b.get("name") or "tool",
                               "arg": tool_arg(b.get("input"))})
    return (who, blocks) if blocks else None


def parse_claude(path):
    """One pass. The turn count needs the whole file anyway, and `ai-title`
    records appear well past the head, so reading only the head would cost the
    better title for no saving."""
    sid = cwd = branch = ai_title = first_user = None
    turns = 0
    for d in iter_json_lines(path):
        if d.get("type") == "ai-title":
            ai_title = d.get("aiTitle") or ai_title
        sid = sid or d.get("sessionId")
        turn = claude_turn(d)
        if turn is None:
            continue
        who, blocks = turn
        cwd = cwd or d.get("cwd")
        if not branch and d.get("gitBranch"):
            branch = d.get("gitBranch")
        turns += 1
        if who == "you" and first_user is None and not d.get("isMeta"):
            for b in blocks:
                if b["kind"] == "text":
                    first_user = b["text"][:200]
                    break

    if not sid:
        sid = os.path.splitext(os.path.basename(path))[0]
    if not cwd:
        cwd = decode_project_dir(os.path.basename(os.path.dirname(path)))
    return {
        "id": sid,
        "cwd": cwd,
        "branch": branch or "",
        "title": ai_title or first_user or "(untitled)",
        "turns": turns,
    }


def read_claude_turns(path):
    out = []
    for d in iter_json_lines(path):
        turn = claude_turn(d)
        if turn:
            out.append({"who": turn[0], "blocks": turn[1]})
    return out


def scan_claude():
    out = []
    for entry in scandir(CLAUDE_ROOT):
        if not entry.is_dir():
            continue
        for f in scandir(entry.path):
            if f.is_file() and f.name.endswith(".jsonl"):
                out.append(f)
    return out


# ---------------------------------------------------------------- pi

def pi_turn(d):
    if d.get("type") != "message":
        return None
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    role = msg.get("role")
    if role not in ("user", "assistant"):
        return None
    who = "you" if role == "user" else "agent"
    content = msg.get("content")
    blocks = []
    if isinstance(content, str):
        txt = clean_title(content) if who == "you" else content.strip()
        if txt:
            blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text") or ""
                txt = clean_title(txt) if who == "you" else txt.strip()
                if txt:
                    blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
            elif bt in ("tool_use", "tool_call", "toolCall"):
                blocks.append({"kind": "tool",
                               "verb": b.get("name") or b.get("toolName") or "tool",
                               "arg": tool_arg(b.get("input") or b.get("arguments"))})
    err = msg.get("errorMessage")
    if err and not blocks:
        blocks.append({"kind": "text", "text": str(err)[:MAX_TEXT]})
    return (who, blocks) if blocks else None


def parse_pi(path):
    sid = cwd = title = None
    turns = 0
    for d in iter_json_lines(path):
        if d.get("type") == "session":
            sid = d.get("id") or sid
            cwd = d.get("cwd") or cwd
            continue
        turn = pi_turn(d)
        if turn is None:
            continue
        who, blocks = turn
        turns += 1
        if who == "you" and title is None:
            for b in blocks:
                if b["kind"] == "text":
                    title = b["text"][:200]
                    break

    if not sid:
        # 2026-09-10T15-41-45-831Z_01a08bfb-....jsonl
        stem = os.path.splitext(os.path.basename(path))[0]
        sid = stem.split("_", 1)[1] if "_" in stem else stem
    if not cwd:
        cwd = decode_pi_project_dir(os.path.basename(os.path.dirname(path)))
    return {"id": sid, "cwd": cwd, "branch": "", "title": title or "(untitled)", "turns": turns}


def decode_pi_project_dir(name):
    """--Users-me-dev-repo--  ->  /Users/me/dev/repo"""
    if not name:
        return ""
    return decode_project_dir("-" + name.strip("-"))


def read_pi_turns(path):
    out = []
    for d in iter_json_lines(path):
        turn = pi_turn(d)
        if turn:
            out.append({"who": turn[0], "blocks": turn[1]})
    return out


def scan_pi():
    out = []
    for entry in scandir(PI_ROOT):
        if not entry.is_dir():
            continue
        for f in scandir(entry.path):
            if f.is_file() and f.name.endswith(".jsonl"):
                out.append(f)
    return out


# ---------------------------------------------------------------- cursor cli

def scan_cursor():
    """~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl"""
    out = []
    for proj in scandir(CURSOR_TRANSCRIPTS):
        if not proj.is_dir():
            continue
        troot = os.path.join(proj.path, "agent-transcripts")
        for sess in scandir(troot):
            if not sess.is_dir():
                continue
            for f in scandir(sess.path):
                if f.is_file() and f.name.endswith(".jsonl"):
                    out.append(f)
    return out


_cursor_meta_lock = threading.Lock()
_cursor_meta = {}          # session-id -> {"name": str}
_cursor_meta_stamp = 0.0


def cursor_meta(sid):
    """Session name lives in ~/.cursor/chats/<md5(project)>/<id>/store.db, meta
    key '0', hex-encoded JSON. Opened read-only; WAL means it can lag."""
    global _cursor_meta_stamp
    with _cursor_meta_lock:
        if time.time() - _cursor_meta_stamp > 30:
            _cursor_meta.clear()
            _cursor_meta_stamp = time.time()
        if sid in _cursor_meta:
            return _cursor_meta[sid]

    info = {}
    for wshash in scandir(CURSOR_CHATS):
        if not wshash.is_dir():
            continue
        db = os.path.join(wshash.path, sid, "store.db")
        if not os.path.exists(db):
            continue
        try:
            uri = "file:" + urllib.parse.quote(db) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=1.0)
            try:
                row = con.execute("SELECT value FROM meta WHERE key='0' LIMIT 1").fetchone()
            finally:
                con.close()
            if row and row[0]:
                meta = json.loads(bytes.fromhex(row[0]).decode("utf-8"))
                if isinstance(meta, dict):
                    info = {"name": meta.get("name") or ""}
        except (sqlite3.Error, ValueError, OSError, UnicodeDecodeError):
            info = {}
        break

    with _cursor_meta_lock:
        _cursor_meta[sid] = info
    return info


def cursor_cwd(path):
    """<project> under ~/.cursor/projects is the workspace folder name, not a
    full path. Recover the real path by matching md5(candidate) against the
    ~/.cursor/chats workspace hash for this session."""
    sid = os.path.splitext(os.path.basename(path))[0]
    proj = path
    for _ in range(3):
        proj = os.path.dirname(proj)
    name = os.path.basename(proj)

    for wshash in scandir(CURSOR_CHATS):
        if wshash.is_dir() and os.path.exists(os.path.join(wshash.path, sid, "store.db")):
            guess = _reverse_workspace_hash(wshash.name, name)
            if guess:
                return guess
            break
    return decode_project_dir(name) if name.startswith("-") else os.path.join(HOME, name)


def _reverse_workspace_hash(wshash, folder):
    """md5 is one-way, so probe the plausible parents of a folder with that name."""
    roots = [HOME, os.path.join(HOME, "dev"), os.path.join(HOME, "Documents"),
             os.path.join(HOME, "Documents", "Projects"), os.path.join(HOME, "src"),
             os.path.join(HOME, "code"), os.path.join(HOME, "Developer"), "/tmp"]
    for root in roots:
        cand = os.path.join(root, folder)
        if hashlib.md5(cand.encode("utf-8")).hexdigest() == wshash:
            return cand
    return None


def cursor_turn(d):
    role = (d.get("role") or "").lower()
    if role not in ("user", "assistant"):
        return None
    who = "you" if role == "user" else "agent"
    msg = d.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    blocks = []
    if isinstance(content, str):
        txt = clean_title(content) if who == "you" else content.strip()
        if txt:
            blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = (b.get("type") or "").lower()
            if bt == "text":
                txt = b.get("text") or ""
                txt = clean_title(txt) if who == "you" else txt.strip()
                if txt:
                    blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
            elif bt in ("tool_use", "tool_call", "tool-use"):
                blocks.append({"kind": "tool", "verb": b.get("name") or "tool",
                               "arg": tool_arg(b.get("input") or b.get("arguments"))})
    return (who, blocks) if blocks else None


def parse_cursor(path):
    sid = os.path.splitext(os.path.basename(path))[0]
    title = None
    turns = 0
    for d in iter_json_lines(path):
        turn = cursor_turn(d)
        if turn is None:
            continue
        who, blocks = turn
        turns += 1
        if who == "you" and title is None:
            for b in blocks:
                if b["kind"] == "text":
                    title = b["text"][:200]
                    break
    name = cursor_meta(sid).get("name") or ""
    return {"id": sid, "cwd": cursor_cwd(path), "branch": "",
            "title": name or title or "(untitled)", "turns": turns}


def read_cursor_turns(path):
    out = []
    for d in iter_json_lines(path):
        turn = cursor_turn(d)
        if turn:
            out.append({"who": turn[0], "blocks": turn[1]})
    return out


# ---------------------------------------------------------------- antigravity cli

def scan_antigravity():
    """~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl.
    The conversation itself is a protobuf-in-sqlite store next door; this log is
    the same steps in a named-field form, appended as they finish."""
    out = []
    for conv in scandir(AG_BRAIN):
        if not conv.is_dir():
            continue
        logs = os.path.join(conv.path, ".system_generated", "logs")
        for f in scandir(logs):
            # transcript_full.jsonl repeats these steps with untruncated output.
            if f.is_file() and f.name == "transcript.jsonl":
                out.append(f)
    return out


_ag_meta_lock = threading.Lock()
_ag_meta = {}              # conversation-id -> {"title": str, "cwd": str}
_ag_meta_stamp = 0.0


def antigravity_meta(cid):
    """Title and workspace for one conversation. Neither is in the transcript."""
    global _ag_meta_stamp
    with _ag_meta_lock:
        if time.time() - _ag_meta_stamp < 30:
            return _ag_meta.get(cid, {})
    built = _antigravity_meta_all()
    with _ag_meta_lock:
        _ag_meta.clear()
        _ag_meta.update(built)
        _ag_meta_stamp = time.time()
    return built.get(cid, {})


def _antigravity_meta_all():
    """conversation_summaries.db holds the titles, but it is only rewritten now
    and then and lags the newest sessions by days. history.jsonl records the
    workspace with every prompt the user sends, so it wins on the workspace."""
    out = {}
    try:
        uri = "file:" + urllib.parse.quote(AG_SUMMARIES) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            rows = con.execute("SELECT conversation_id, title, preview, "
                               "workspace_uris FROM conversation_summaries").fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        rows = []
    for cid, title, preview, uris in rows:
        out[cid] = {"title": title or preview or "",
                    "cwd": _antigravity_workspace(uris)}

    for d in iter_json_lines(AG_HISTORY):
        cid, ws = d.get("conversationId"), d.get("workspace")
        if cid and ws:
            out.setdefault(cid, {"title": "", "cwd": ""})["cwd"] = ws
    return out


def _antigravity_workspace(uris):
    """A JSON array of file:// URIs. The first one is the session's root."""
    try:
        arr = json.loads(uris or "[]")
    except ValueError:
        return ""
    for u in arr if isinstance(arr, list) else []:
        if isinstance(u, str) and u.startswith("file://"):
            return urllib.parse.unquote(u[len("file://"):])
    return ""


# Which Antigravity tool argument best describes a call, in preference order.
_AG_ARG_KEYS = ("CommandLine", "AbsolutePath", "TargetFile", "DirectoryPath",
                "Query", "query", "SearchPath", "Url", "Description", "toolAction")


def antigravity_tool_arg(args):
    """Argument values are stored as JSON text, so a path arrives quoted."""
    if not isinstance(args, dict):
        return ""
    for k in _AG_ARG_KEYS:
        raw = args.get(k)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            val = json.loads(raw)
        except ValueError:
            val = raw
        if isinstance(val, str) and val.strip():
            return _WS.sub(" ", val.strip())[:200]
    return ""


def antigravity_turn(d):
    """Every tool result is its own step here. Only the prompt and the planner
    step that answers it carry anything a reader wants back."""
    typ = d.get("type")
    blocks = []
    if typ == "USER_INPUT":
        who = "you"
        # The prompt arrives wrapped in <USER_REQUEST> alongside a timestamp and
        # any settings the user changed; clean_title drops the scaffolding.
        txt = clean_title(d.get("content") or "")
        if txt:
            blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
    elif typ == "PLANNER_RESPONSE":
        who = "agent"
        txt = (d.get("content") or "").strip()
        if txt:
            blocks.append({"kind": "text", "text": txt[:MAX_TEXT]})
        for tc in d.get("tool_calls") or []:
            if isinstance(tc, dict):
                blocks.append({"kind": "tool", "verb": tc.get("name") or "tool",
                               "arg": antigravity_tool_arg(tc.get("args"))})
    else:
        return None
    return (who, blocks) if blocks else None


def parse_antigravity(path):
    # .../brain/<conversation-id>/.system_generated/logs/transcript.jsonl
    cid = path
    for _ in range(3):
        cid = os.path.dirname(cid)
    cid = os.path.basename(cid)

    first_user = None
    turns = 0
    for d in iter_json_lines(path):
        turn = antigravity_turn(d)
        if turn is None:
            continue
        who, blocks = turn
        turns += 1
        if who == "you" and first_user is None:
            for b in blocks:
                if b["kind"] == "text":
                    first_user = b["text"][:200]
                    break

    meta = antigravity_meta(cid)
    return {"id": cid, "cwd": meta.get("cwd") or "", "branch": "",
            "title": meta.get("title") or first_user or "(untitled)", "turns": turns}


def read_antigravity_turns(path):
    out = []
    for d in iter_json_lines(path):
        turn = antigravity_turn(d)
        if turn:
            out.append({"who": turn[0], "blocks": turn[1]})
    return out


# ---------------------------------------------------------------- scan + cache

SCANNERS = {"claude": scan_claude, "pi": scan_pi, "cursor": scan_cursor,
            "antigravity": scan_antigravity}
PARSERS = {"claude": parse_claude, "pi": parse_pi, "cursor": parse_cursor,
           "antigravity": parse_antigravity}
READERS = {"claude": read_claude_turns, "pi": read_pi_turns,
           "cursor": read_cursor_turns, "antigravity": read_antigravity_turns}


def scandir(path):
    try:
        with os.scandir(path) as it:
            return list(it)
    except OSError:
        return []


_cache = {}                     # path -> {mtime,size,src,id,cwd,branch,title,turns}
_cache_lock = threading.Lock()
_index = {}                     # (src, id) -> path
_last_scan = 0.0
_scan_ms = 0.0


def scan_all():
    """Stat everything; re-parse only what changed. Evict what is gone."""
    global _last_scan, _scan_ms, _index
    started = time.time()
    seen = set()
    rows = []

    for src in SOURCES:
        for entry in SCANNERS[src]():
            path = entry.path
            try:
                st = entry.stat()
            except OSError:
                continue
            seen.add(path)
            with _cache_lock:
                hit = _cache.get(path)
            if hit and hit["mtime"] == st.st_mtime and hit["size"] == st.st_size:
                rec = hit
            else:
                try:
                    parsed = PARSERS[src](path)
                except Exception as exc:                       # a bad file is not fatal
                    log("parse failed %s: %s" % (path, exc))
                    continue
                rec = dict(parsed)
                rec["src"] = src
                rec["mtime"] = st.st_mtime
                rec["size"] = st.st_size
                with _cache_lock:
                    _cache[path] = rec
            if rec["turns"] <= 0:
                continue
            rows.append((path, rec))

    with _cache_lock:
        for gone in [p for p in _cache if p not in seen]:
            del _cache[gone]

    rows.sort(key=lambda pr: (-pr[1]["mtime"], pr[0]))
    _index = {(r["src"], r["id"]): p for p, r in rows}
    _last_scan = time.time()
    _scan_ms = (_last_scan - started) * 1000.0
    return rows


def session_list():
    rows = scan_all()
    now = time.time()
    out = []
    for path, r in rows:
        out.append({
            "id": r["id"],
            "src": r["src"],
            "title": r["title"],
            "path": pretty_path(r["cwd"]),
            "branch": r["branch"],
            "turns": r["turns"],
            "mtime": r["mtime"],
            "live": (now - r["mtime"]) < LIVE_WINDOW,
            "cmd": resume_command(r["src"], r["cwd"], r["id"]),
        })
    return out


def pretty_path(p):
    if p and p.startswith(HOME):
        return "~" + p[len(HOME):]
    return p or ""


def find_session(src, sid):
    if src not in SOURCES or not ID_RE.match(sid or ""):
        return None, None
    path = _index.get((src, sid))
    if path is None:
        scan_all()
        path = _index.get((src, sid))
    if path is None:
        return None, None
    with _cache_lock:
        rec = _cache.get(path)
    return path, rec


# ---------------------------------------------------------------- http

def log(msg):
    sys.stderr.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "session-viewer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # -- helpers

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    # The two names that resolve to this loopback socket.
    def _authorities(self):
        return ("%s:%d" % (HOST, PORT), "localhost:%d" % PORT)

    def _host_ok(self):
        return self.headers.get("Host", "") in self._authorities()

    def _auth_ok(self):
        return self.headers.get("X-Session-Viewer-Token", "") == TOKEN

    # -- routes

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/health":
            return self._json(200, {"ok": True, "sessions": len(_index),
                                    "scan_ms": round(_scan_ms, 1)})

        if path == "/":
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            page = (PAGE.replace("__TOKEN__", TOKEN)
                        .replace("__ICONS__", json.dumps(ICONS)))
            return self._send(200, page, "text/html; charset=utf-8")

        if not path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if not self._host_ok():
            return self._json(403, {"error": "bad host"})
        if not self._auth_ok():
            return self._json(401, {"error": "bad token"})

        if path == "/api/sessions":
            try:
                items = session_list()
            except Exception as exc:
                log("scan failed: %s" % exc)
                return self._json(500, {"error": str(exc)})
            return self._json(200, {"sessions": items, "scanned_at": _last_scan,
                                    "now": time.time(), "scan_ms": round(_scan_ms, 1)})

        m = re.match(r"^/api/session/([a-z]+)/([^/]+)$", path)
        if m:
            src = m.group(1)
            sid = urllib.parse.unquote(m.group(2))
            fpath, rec = find_session(src, sid)
            if not rec:
                return self._json(404, {"error": "no such session"})
            try:
                turns = READERS[src](fpath)
            except Exception as exc:
                log("read failed %s: %s" % (fpath, exc))
                return self._json(500, {"error": "could not read transcript: %s" % exc})
            truncated = len(turns) > MAX_TURNS
            if truncated:
                turns = turns[-MAX_TURNS:]
            now = time.time()
            return self._json(200, {
                "id": rec["id"], "src": src, "title": rec["title"],
                "path": pretty_path(rec["cwd"]), "branch": rec["branch"],
                "turns_total": rec["turns"], "mtime": rec["mtime"], "now": now,
                "live": (now - rec["mtime"]) < LIVE_WINDOW,
                "cmd": resume_command(src, rec["cwd"], rec["id"]),
                "truncated": truncated, "transcript": turns,
            })

        return self._json(404, {"error": "not found"})

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Harness Sessions — local viewer</title>
<style>
:root{
  /* Aura dark */
  color-scheme:dark;
  --ink:#EDECEE;
  --ink-2:#BDBAC6;
  --muted:#7E7A8C;
  --surface:#15141B;
  --panel:#1B1A23;
  --rule:#2A2839;
  --rule-strong:#3B3850;
  --sel:#2E2A44;
  --hover:#232131;
  --accent:#A277FF;
  --accent-hi:#B995FF;
  --live:#61FFCA;
  --warn:#FFCA85;
  --err:#FF6767;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  --sans:system-ui,-apple-system,Segoe UI,sans-serif;
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0;
  background:var(--surface);
  color:var(--ink);
  font-family:var(--sans);
  font-size:14px;
  line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
button{font:inherit;color:inherit;cursor:pointer}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* ---------- shell ---------- */
.shell{display:flex;flex-direction:column;height:100vh;height:100dvh}

.topbar{
  display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding:14px 20px 12px;
  border-bottom:1px solid var(--rule);
  background:var(--panel);
}
.wordmark{font-weight:600;font-size:15px;letter-spacing:-.01em}
.topbar .spacer{flex:1 1 auto}

.scan{display:flex;align-items:center;gap:10px}
.scan-age{font-family:var(--mono);font-size:12px;color:var(--muted)}
.scan-age[data-stale="true"]{color:var(--warn)}
.refresh{
  border:1px solid var(--rule-strong);background:var(--panel);
  border-radius:3px;padding:4px 11px;font-size:13px;font-weight:500;
}
.refresh:hover{background:var(--hover)}
.refresh:active{background:var(--sel)}
.refresh[data-busy="true"]{opacity:.55;cursor:default}

/* ---------- search ---------- */
.search{position:relative;display:flex;align-items:center;flex:0 1 300px;min-width:180px}
.search .glass{position:absolute;left:9px;width:13px;height:13px;color:var(--muted);pointer-events:none}
.search input{
  width:100%;font:inherit;font-size:13px;color:var(--ink);
  background:var(--surface);border:1px solid var(--rule-strong);border-radius:3px;
  padding:4px 30px 4px 28px;
}
.search input::placeholder{color:var(--muted)}
.search input::-webkit-search-cancel-button,
.search input::-webkit-search-decoration{-webkit-appearance:none;display:none}
.search kbd{
  position:absolute;right:7px;font-family:var(--mono);font-size:11px;line-height:15px;
  color:var(--muted);border:1px solid var(--rule-strong);border-radius:3px;
  padding:0 5px;pointer-events:none;
}
.search:focus-within kbd{display:none}
.search kbd[hidden]{display:none}
.search .clear{
  position:absolute;right:5px;display:inline-flex;align-items:center;justify-content:center;
  width:19px;height:19px;padding:0;border:0;border-radius:3px;
  background:transparent;color:var(--muted);
}
.search .clear:hover{background:var(--hover);color:var(--ink)}
.search .clear[hidden]{display:none}
mark{background:var(--sel);color:var(--accent-hi);border-radius:2px;padding:0 1px}

/* ---------- source filters ---------- */
.sources{
  display:flex;gap:6px;flex-wrap:wrap;justify-content:center;
}
.src{
  display:inline-flex;align-items:center;gap:7px;
  border:1px solid var(--rule-strong);background:var(--panel);
  border-radius:3px;padding:3px 10px 3px 8px;font-size:13px;
  color:var(--ink-2);
}
.src:hover{background:var(--hover)}
.src[aria-pressed="true"]{background:var(--sel);border-color:var(--accent);color:var(--ink);font-weight:500}
.src[aria-pressed="false"]{opacity:.55}
.src .swatch{width:14px;height:14px;flex:none}
.src .n{font-family:var(--mono);font-size:12px;color:var(--muted)}
.src[aria-pressed="true"] .n{color:var(--ink-2)}

/* ---------- main split ---------- */
.split{display:grid;grid-template-columns:minmax(400px,1fr) 1.05fr;flex:1 1 auto;min-height:0}
.listcol{display:flex;flex-direction:column;min-height:0;min-width:0;border-right:1px solid var(--rule)}
.rows{overflow-y:auto;flex:1 1 auto;min-height:0}

/* ---------- session row ---------- */
.row{
  display:grid;
  grid-template-columns:12px minmax(0,1fr) auto;
  grid-template-areas:"mark title meta" "mark sub meta";
  column-gap:11px;
  width:100%;text-align:left;
  padding:9px 16px 10px 12px;
  border:0;border-bottom:1px solid var(--rule);
  background:transparent;
}
.row:hover{background:var(--hover)}
.row[aria-current="true"]{background:var(--sel);box-shadow:inset 2px 0 0 var(--accent)}
.mark{grid-area:mark;align-self:start;margin-top:4px;width:14px;height:14px;flex:none}
.row[data-live="true"] .mark{animation:breathe 2.4s ease-in-out infinite}
@keyframes breathe{0%,100%{opacity:1}50%{opacity:.35}}
/* The Cursor logo is solid black on transparent, which is invisible on this
   surface. Every other mark already carries its own colour. */
.mark[data-src="cursor"],.src .swatch[data-src="cursor"]{filter:invert(1)}
@media (prefers-reduced-motion:reduce){.row[data-live="true"] .mark{animation:none}}

.title{grid-area:title;font-size:13.5px;line-height:1.35;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sub{grid-area:sub;display:flex;gap:9px;align-items:baseline;font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-top:2px;overflow:hidden}
.sub .path{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:0 1 auto}
.sub .branch{flex:none;color:var(--ink-2)}
.sub .turns{flex:none}
.meta{grid-area:meta;align-self:center;font-family:var(--mono);font-size:11.5px;color:var(--muted);text-align:right;white-space:nowrap;padding-left:8px}
.row[data-live="true"] .meta{color:var(--live);font-weight:500}

.empty{padding:44px 20px;color:var(--muted);max-width:34ch}
.empty strong{display:block;color:var(--ink);font-weight:600;margin-bottom:4px}
.empty code{font-family:var(--mono);font-size:12px;color:var(--ink-2)}
.empty[data-kind="error"] strong{color:var(--err)}

/* ---------- pager ---------- */
.pager{
  display:flex;align-items:center;gap:10px;flex:none;
  padding:8px 16px;border-top:1px solid var(--rule);background:var(--panel);
}
.range{font-family:var(--mono);font-size:12px;color:var(--muted)}
.pager .spacer{flex:1 1 auto}
.pg{
  border:1px solid transparent;background:transparent;border-radius:3px;
  min-width:26px;height:26px;padding:0 6px;
  font-family:var(--mono);font-size:12px;color:var(--ink-2);
}
.pg:hover:not(:disabled){background:var(--hover);border-color:var(--rule-strong)}
.pg[aria-current="true"]{background:var(--accent);color:var(--surface);font-weight:600}
.pg:disabled{opacity:.3;cursor:default}
.gap{font-family:var(--mono);font-size:12px;color:var(--muted);padding:0 2px;align-self:center}

/* ---------- detail ---------- */
.detail{display:flex;flex-direction:column;min-height:0;min-width:0;background:var(--panel)}
.dbody{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
.dbody[hidden]{display:none}   /* author display would otherwise beat the UA sheet */
.dhead{flex:none;padding:16px 22px 14px;border-bottom:1px solid var(--rule)}
.dtitle{font-size:16px;font-weight:600;line-height:1.35;letter-spacing:-.01em;margin:0 0 7px;max-width:62ch}
.dfacts{display:flex;flex-wrap:wrap;gap:4px 14px;font-family:var(--mono);font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.dfacts b{font-weight:400;color:var(--ink-2)}

.cmdblock{margin:14px 0 0}
.cmd{
  display:flex;align-items:center;gap:8px;
  background:var(--surface);border:1px solid var(--rule-strong);border-radius:3px;
  padding:6px 6px 6px 11px;color:var(--ink);
}
.cmdtext{
  font-family:var(--mono);font-size:12.5px;line-height:1.5;
  overflow-x:auto;white-space:pre;flex:1;min-width:0;
}
.copyIcon{
  flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;
  width:26px;height:26px;padding:0;
  border:1px solid var(--rule-strong);background:var(--panel);border-radius:3px;
  color:var(--ink-2);cursor:pointer;
}
.copyIcon:hover{background:var(--hover);color:var(--ink)}
.actions{display:flex;gap:8px;margin-top:9px;align-items:center;flex-wrap:wrap}
.btn{
  border:1px solid var(--rule-strong);background:var(--panel);
  border-radius:3px;padding:5px 13px;font-size:13px;font-weight:500;
}
.btn:hover{background:var(--hover)}
.btn.primary{background:var(--accent);color:var(--surface);border-color:var(--accent)}.btn.primary:hover{background:var(--accent-hi);border-color:var(--accent-hi)}
.btn:disabled{opacity:.5;cursor:default}
.btn[aria-pressed]{display:inline-flex;align-items:center;gap:7px}
.btn[aria-pressed="true"]{background:var(--sel);border-color:var(--accent)}
.btn[aria-pressed] .n{font-family:var(--mono);font-size:12px;color:var(--muted)}
.btn[aria-pressed="true"] .n{color:var(--ink-2)}
.flash{font-size:12.5px;color:var(--ink-2);opacity:0;transition:opacity .18s}
.flash.on{opacity:1}
.flash[data-kind="error"]{color:var(--err)}
@media (prefers-reduced-motion:reduce){.flash{transition:none}}
.note{
  margin-top:9px;font-size:12.5px;color:var(--warn);
  border-left:2px solid currentColor;padding-left:9px;max-width:56ch;
}
.livenote{margin-top:9px;font-size:12.5px;color:var(--ink-2)}

.transcript{overflow-y:auto;flex:1 1 auto;min-height:0;padding:6px 22px 40px}
.turn{padding:13px 0;border-bottom:1px solid var(--rule)}
.turn:last-child{border-bottom:0}
.who{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin-bottom:5px}
.turn[data-who="you"] .who{color:var(--ink)}
.turn p{margin:0 0 8px;max-width:74ch;white-space:pre-wrap;overflow-wrap:anywhere}
.turn p:last-child{margin-bottom:0}
.tool{
  display:flex;gap:9px;align-items:baseline;
  font-family:var(--mono);font-size:12px;color:var(--ink-2);
  background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  padding:5px 9px;margin:0 0 6px;
}
.tool .verb{color:var(--muted);flex:none}
.tool .arg{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.transcript[data-tools="off"] .tool{display:none}
.transcript[data-tools="off"] .turn[data-toolonly="true"]{display:none}
.placeholder{display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);padding:30px;text-align:center}

.back{display:none}

/* ---------- narrow ---------- */
@media (max-width:900px){
  .search{flex:1 1 100%}
  .split{grid-template-columns:1fr}
  .listcol{border-right:0}
  .detail{
    position:fixed;inset:0;z-index:20;
    transform:translateX(100%);transition:transform .2s ease;
  }
  .detail.open{transform:translateX(0)}
  @media (prefers-reduced-motion:reduce){.detail{transition:none}}
  .back{display:inline-block;margin-bottom:10px;border:1px solid var(--rule-strong);background:var(--panel);border-radius:3px;padding:4px 11px;font-size:13px}
  .placeholder{display:none}
}
</style>
</head>
<body>
<div class="shell">

  <header class="topbar">
    <span class="wordmark">Harness Sessions</span>
    <label class="search">
      <svg class="glass" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/></svg>
      <input id="q" type="search" placeholder="Search sessions"
             autocomplete="off" spellcheck="false" aria-label="Search sessions">
      <kbd id="qHint">/</kbd>
      <button class="clear" id="qClear" hidden title="Clear search" aria-label="Clear search">
        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </label>
    <span class="spacer"></span>
    <nav class="sources" id="sources" aria-label="Session sources"></nav>
    <span class="spacer"></span>
    <div class="scan">
      <span class="scan-age" id="scanAge">scanning…</span>
      <button class="refresh" id="refreshBtn">Refresh</button>
    </div>
  </header>

  <div class="split">
    <div class="listcol">
      <div class="rows" id="rows" role="list"></div>
      <div class="pager">
        <span class="range" id="range"></span>
        <span class="spacer"></span>
        <div id="pages" style="display:flex;gap:3px"></div>
      </div>
    </div>

    <section class="detail" id="detail" aria-label="Session detail">
      <div class="placeholder" id="placeholder">Select a session to read its transcript.</div>
      <div id="detailBody" class="dbody" hidden></div>
    </section>
  </div>

</div>

<script>
const TOKEN = "__TOKEN__";
const PER_PAGE = 50;

const ICONS = __ICONS__;

const SOURCES = {
  claude:      {label:"Claude Code",     icon:ICONS.claude},
  pi:          {label:"Pi",              icon:ICONS.pi},
  cursor:      {label:"Cursor CLI",      icon:ICONS.cursor},
  antigravity: {label:"Antigravity CLI", icon:ICONS.antigravity}
};

// Sources whose resume flag comes from upstream docs rather than a run here.
const UNVERIFIED = {};

let ALL = [];
let active = {claude:true, pi:true, cursor:true, antigravity:true};
let query = "";              // raw text in the field
let terms = [];              // lowercased words, every one has to match
let qre = null;              // the same words as one regex, for highlighting
let page = 0;
let selectedKey = null;      // "src/id"
let scannedAt = 0;           // server epoch seconds
let clockSkew = 0;           // serverNow - clientNow
let listError = null;
let loaded = false;
let pollTimer = null;
let showTools = loadShowTools();   // the commands the agent ran; off by default

// A per-viewer convenience, so it survives a reload. Reading it can throw
// outright in a private window or with site data blocked.
function loadShowTools(){
  try{ return localStorage.getItem("session-viewer:showTools") === "1"; }
  catch(e){ return false; }
}
function saveShowTools(v){
  try{ localStorage.setItem("session-viewer:showTools", v ? "1" : "0"); }
  catch(e){ /* the toggle still works for this page view */ }
}

const $ = (id)=>document.getElementById(id);
const key = (s)=>s.src+"/"+s.id;

function esc(s){
  return String(s==null?"":s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

function escRe(s){ return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

// Escapes like esc() and wraps the query hits. Never build this from raw text.
function hl(s){
  const str = String(s==null?"":s);
  if(!qre) return esc(str);
  qre.lastIndex = 0;
  let out = "", last = 0, m;
  while((m = qre.exec(str)) !== null){
    if(m[0] === ""){ qre.lastIndex++; continue; }   // never spin on an empty match
    out += esc(str.slice(last, m.index)) + "<mark>" + esc(m[0]) + "</mark>";
    last = m.index + m[0].length;
  }
  return out + esc(str.slice(last));
}

async function api(path, opts){
  opts = opts || {};
  const headers = Object.assign({"X-Session-Viewer-Token": TOKEN}, opts.headers||{});
  const res = await fetch(path, Object.assign({}, opts, {headers}));
  let data = null;
  try { data = await res.json(); } catch(e){ /* fall through to status text */ }
  if(!res.ok) throw new Error((data && data.error) || (res.status+" "+res.statusText));
  return data;
}

/* ---------- time ---------- */
function serverNow(){ return Date.now()/1000 + clockSkew; }

function age(secs){
  const m = Math.max(0, Math.round(secs/60));
  if(m < 1) return "now";
  if(m < 60) return m+"m";
  if(m < 1440) return Math.round(m/60)+"h";
  if(m < 10080) return Math.round(m/1440)+"d";
  return Math.round(m/10080)+"w";
}

/* ---------- data ---------- */
async function load(){
  const btn = $("refreshBtn");
  btn.dataset.busy = "true"; btn.disabled = true;
  try{
    const d = await api("/api/sessions");
    ALL = d.sessions;
    scannedAt = d.scanned_at;
    clockSkew = d.now - Date.now()/1000;
    listError = null;
  }catch(err){
    listError = err.message;
  }finally{
    loaded = true;
    btn.dataset.busy = "false"; btn.disabled = false;
  }
  drawSources(); drawRows(); tickScan();
  // A selected session may have vanished from disk between scans.
  if(selectedKey && !ALL.some(s=>key(s)===selectedKey)) { /* detail keeps its last render */ }
}

// What the field matches against. The id is in here so a pasted session id lands.
function haystack(s){
  return (s.title+" "+s.path+" "+(s.branch||"")+" "+s.id+" "+SOURCES[s.src].label).toLowerCase();
}

function searched(){
  if(!terms.length) return ALL;
  return ALL.filter(s=>{ const h = haystack(s); return terms.every(t=>h.indexOf(t) >= 0); });
}

function setQuery(v){
  query = v;
  terms = v.trim().toLowerCase().split(/\s+/).filter(Boolean);
  qre = terms.length ? new RegExp(terms.map(escRe).join("|"), "gi") : null;
  page = 0;
  $("qClear").hidden = !v;
  $("qHint").hidden = !!v;
  drawSources(); drawRows();
}

/* ---------- sources ---------- */
function drawSources(){
  const el = $("sources");
  el.innerHTML = "";
  // Counts follow the search, so a chip says what switching it on would add.
  const pool = searched();
  for(const k of Object.keys(SOURCES)){
    const n = pool.filter(s=>s.src===k).length;
    const b = document.createElement("button");
    b.className = "src";
    b.setAttribute("aria-pressed", String(active[k]));
    b.innerHTML = `<img class="swatch" data-src="${esc(k)}" src="${SOURCES[k].icon}" alt="">${esc(SOURCES[k].label)} <span class="n">${n}</span>`;
    b.onclick = ()=>{
      const on = Object.values(active).filter(Boolean).length;
      if(active[k] && on===1) return;     // never filter everything away
      active[k] = !active[k]; page = 0; drawSources(); drawRows();
    };
    el.appendChild(b);
  }
}

/* ---------- rows ---------- */
function drawRows(){
  const hits = searched();
  const list = hits.filter(s=>active[s.src]);
  const pages = Math.max(1, Math.ceil(list.length/PER_PAGE));
  if(page > pages-1) page = pages-1;
  const start = page*PER_PAGE;
  const slice = list.slice(start, start+PER_PAGE);

  const rows = $("rows");
  rows.innerHTML = "";

  if(listError){
    rows.innerHTML = `<div class="empty" data-kind="error"><strong>Could not read the session list.</strong>${esc(listError)}. The service log is the only place the detail shows once it is detached — check <code>~/Library/Logs/session-viewer.log</code>, then press Refresh.</div>`;
  }else if(!loaded){
    rows.innerHTML = `<div class="empty"><strong>Scanning.</strong>Reading session files from disk.</div>`;
  }else if(!list.length){
    rows.innerHTML =
      hits.length
        ? `<div class="empty"><strong>Nothing from these sources.</strong>Every match belongs to a source you have switched off.</div>`
      : terms.length
        ? `<div class="empty"><strong>No match.</strong>Nothing matches <code>${esc(query.trim())}</code>. The search reads the title, project path, branch, source, and session id.</div>`
      : ALL.length
        ? `<div class="empty"><strong>Nothing from these sources.</strong>Every session on disk belongs to a source you have switched off.</div>`
        : `<div class="empty"><strong>Nothing on disk yet.</strong>Start a session in any of the enabled agents, then refresh.</div>`;
  }

  const now = serverNow();
  for(const s of slice){
    const b = document.createElement("button");
    b.className = "row";
    b.setAttribute("role","listitem");
    b.dataset.live = String(!!s.live);
    b.setAttribute("aria-current", String(selectedKey===key(s)));
    b.innerHTML = `
      <img class="mark" data-src="${esc(s.src)}" src="${SOURCES[s.src].icon}" alt="${esc(SOURCES[s.src].label)}">
      <span class="title">${hl(s.title)}</span>
      <span class="sub">
        <span class="path">${hl(s.path)}</span>
        ${s.branch ? `<span class="branch">${hl(s.branch)}</span>` : ""}
        <span class="turns">${s.turns} turns</span>
      </span>
      <span class="meta">${s.live ? "active" : esc(age(now - s.mtime))}</span>`;
    b.onclick = ()=>{ select(s); };
    rows.appendChild(b);
  }
  rows.scrollTop = 0;

  const from = list.length ? start+1 : 0;
  $("range").textContent = `${from}–${Math.min(start+PER_PAGE, list.length)} of ${list.length}`;
  drawPager(pages);
}

function drawPager(pages){
  const pg = $("pages");
  pg.innerHTML = "";
  const mk = (label, target, disabled, current)=>{
    const x = document.createElement("button");
    x.className = "pg"; x.textContent = label; x.disabled = !!disabled;
    if(current) x.setAttribute("aria-current","true");
    x.onclick = ()=>{ page = target; drawRows(); };
    pg.appendChild(x);
  };
  const gap = ()=>{ const g=document.createElement("span"); g.className="gap"; g.textContent="…"; pg.appendChild(g); };

  mk("‹", Math.max(0,page-1), page===0);
  // Numbered pages, elided in the middle once there are more than nine.
  let nums;
  if(pages <= 9){
    nums = Array.from({length:pages}, (_,i)=>i);
  }else{
    const set = new Set([0, pages-1, page, page-1, page+1]);
    if(page <= 2) [1,2,3].forEach(i=>set.add(i));
    if(page >= pages-3) [pages-2,pages-3,pages-4].forEach(i=>set.add(i));
    nums = [...set].filter(i=>i>=0 && i<pages).sort((a,b)=>a-b);
  }
  let prev = -1;
  for(const i of nums){
    if(prev >= 0 && i > prev+1) gap();
    mk(String(i+1), i, false, i===page);
    prev = i;
  }
  mk("›", Math.min(pages-1,page+1), page===pages-1);
}

/* ---------- detail ---------- */
async function select(s){
  selectedKey = key(s);
  drawRows();
  $("detail").classList.add("open");
  stopPoll();
  renderDetail(s, null, "loading");
  try{
    const d = await api(`/api/session/${encodeURIComponent(s.src)}/${encodeURIComponent(s.id)}`);
    if(selectedKey !== key(s)) return;      // the user moved on while we fetched
    renderDetail(d, d.transcript, "ok");
    if(d.live) startPoll(s);
  }catch(err){
    if(selectedKey !== key(s)) return;
    renderDetail(s, null, "error", err.message);
  }
}

function renderDetail(s, transcript, state, errMsg){
  const body = $("detailBody"), ph = $("placeholder");
  ph.style.display = "none"; body.hidden = false;

  const now = serverNow();
  const when = s.live ? "still writing"
             : (s.mtime ? "last write "+age(now - s.mtime)+" ago" : "");
  const total = s.turns_total != null ? s.turns_total : s.turns;

  let turnsHTML;
  let toolCount = 0;
  if(state === "loading"){
    turnsHTML = `<div class="empty">Reading the transcript.</div>`;
  }else if(state === "error"){
    turnsHTML = `<div class="empty" data-kind="error"><strong>Could not read this transcript.</strong>${esc(errMsg||"")}. The file may have been rotated or removed since the last scan — press Refresh.</div>`;
  }else if(!transcript.length){
    turnsHTML = `<div class="empty"><strong>No readable turns.</strong>The file exists but holds no user or assistant messages.</div>`;
  }else{
    const parts = transcript.map(t=>{
      const tools = t.blocks.filter(b=> b.kind === "tool").length;
      toolCount += tools;
      const blocks = t.blocks.map(b=> b.kind === "tool"
        ? `<div class="tool"><span class="verb">${esc(b.verb)}</span><span class="arg">${esc(b.arg)}</span></div>`
        : `<p>${esc(b.text)}</p>`).join("");
      const who = t.who === "you" ? "you" : SOURCES[s.src].label;
      const toolOnly = tools === t.blocks.length;
      return `<div class="turn" data-who="${esc(t.who)}" data-toolonly="${toolOnly}"><div class="who">${esc(who)}</div>${blocks}</div>`;
    });
    if(s.truncated) parts.unshift(`<div class="turn"><div class="who">viewer</div><p>Showing the last ${transcript.length} turns of ${total}.</p></div>`);
    turnsHTML = parts.join("");
  }

  body.innerHTML = `
    <div class="dhead">
      <button class="back" id="backBtn">Back to list</button>
      <h1 class="dtitle">${esc(s.title)}</h1>
      <div class="dfacts">
        <span><b>${esc(SOURCES[s.src].label)}</b></span>
        <span>${esc(s.path)}</span>
        ${s.branch ? `<span>${esc(s.branch)}</span>` : ""}
        <span>${total} turns</span>
        ${when ? `<span>${esc(when)}</span>` : ""}
        <span>${esc(s.id)}</span>
      </div>
      <div class="cmdblock">
        <div class="cmd">
          <span class="cmdtext">${esc(s.cmd)}</span>
          <button class="copyIcon" id="copyBtn" title="Copy command" aria-label="Copy command">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>
        </div>
        <div class="actions">
          ${toolCount ? `<button class="btn" id="toolsBtn" aria-pressed="${showTools}">${showTools ? "Hide" : "Show"} commands <span class="n">${toolCount}</span></button>` : ""}
          <span class="flash" id="flash"></span>
        </div>
        ${UNVERIFIED[s.src] ? `<p class="note">The resume flag for ${esc(SOURCES[s.src].label)} has not been verified end to end on this machine. Copy it and check the result before trusting it.</p>` : ""}
        ${s.live ? `<p class="livenote">This session is being written to now. The transcript below updates on its own; the list does not.</p>` : ""}
      </div>
    </div>
    <div class="transcript" id="turnsPane" data-tools="${showTools ? "on" : "off"}">${turnsHTML}</div>`;

  $("backBtn").onclick = ()=> $("detail").classList.remove("open");
  $("copyBtn").onclick = ()=> copyCommand(s.cmd);
  const tb = $("toolsBtn");
  if(tb) tb.onclick = ()=> setShowTools(!showTools, toolCount);
}

function setShowTools(v, toolCount){
  showTools = v;
  saveShowTools(v);
  const pane = $("turnsPane");
  if(pane) pane.dataset.tools = v ? "on" : "off";
  const tb = $("toolsBtn");
  if(tb){
    tb.setAttribute("aria-pressed", String(v));
    tb.innerHTML = `${v ? "Hide" : "Show"} commands <span class="n">${toolCount}</span>`;
  }
}

function flash(msg, kind){
  const f = $("flash");
  if(!f) return;
  f.textContent = msg;
  f.dataset.kind = kind || "";
  f.classList.add("on");
  clearTimeout(flash._t);
  flash._t = setTimeout(()=>f.classList.remove("on"), kind === "error" ? 6000 : 1800);
}

async function copyCommand(cmd){
  // http://127.0.0.1 is a secure context, so the clipboard API is available.
  try{
    await navigator.clipboard.writeText(cmd);
    flash("Copied");
  }catch(e){
    flash("Clipboard refused — select the command above and copy it", "error");
  }
}

/* ---------- live transcript polling ---------- */
function startPoll(s){
  stopPoll();
  pollTimer = setInterval(async ()=>{
    if(selectedKey !== key(s)) return stopPoll();
    try{
      const d = await api(`/api/session/${encodeURIComponent(s.src)}/${encodeURIComponent(s.id)}`);
      if(selectedKey !== key(s)) return stopPoll();
      const pane = $("turnsPane");
      const atBottom = pane && (pane.scrollHeight - pane.scrollTop - pane.clientHeight) < 40;
      const offset = pane ? pane.scrollTop : 0;
      renderDetail(d, d.transcript, "ok");
      const fresh = $("turnsPane");
      if(fresh) fresh.scrollTop = atBottom ? fresh.scrollHeight : offset;
      if(!d.live) stopPoll();
    }catch(e){ stopPoll(); }
  }, 5000);
}
function stopPoll(){ if(pollTimer){ clearInterval(pollTimer); pollTimer = null; } }

/* ---------- scan freshness ---------- */
function tickScan(){
  const el = $("scanAge");
  if(listError){ el.textContent = "scan failed"; el.dataset.stale = "true"; return; }
  if(!scannedAt){ el.textContent = "scanning…"; el.dataset.stale = "false"; return; }
  const secs = Math.max(0, serverNow() - scannedAt);
  el.textContent = secs < 45 ? "scanned just now"
                 : secs < 90 ? "scanned 1 min ago"
                 : "scanned "+Math.floor(secs/60)+" min ago";
  // Detached, there is no "I just started it" anchor, so staleness has to show.
  el.dataset.stale = String(secs >= 300);
}

$("q").oninput = (e)=> setQuery(e.target.value);
$("qClear").onclick = ()=>{ const q = $("q"); q.value = ""; setQuery(""); q.focus(); };

// "/" and cmd/ctrl-K reach the field from anywhere; escape empties it.
document.addEventListener("keydown", (e)=>{
  const q = $("q");
  const el = e.target;
  const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
  if((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)){
    e.preventDefault(); q.focus(); q.select(); return;
  }
  if(e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey){
    e.preventDefault(); q.focus(); q.select(); return;
  }
  if(e.key === "Escape" && el === q){
    if(query){ q.value = ""; setQuery(""); } else { q.blur(); }
  }
});

$("refreshBtn").onclick = ()=> load();
setInterval(tickScan, 5000);
document.addEventListener("visibilitychange", ()=>{ if(!document.hidden) tickScan(); });

load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- main

def main():
    global TOKEN
    TOKEN = load_token()

    rows = scan_all()
    counts = {}
    for _, r in rows:
        counts[r["src"]] = counts.get(r["src"], 0) + 1

    try:
        httpd = Server((HOST, PORT), Handler)
    except OSError as exc:
        sys.exit("cannot bind %s:%d - %s" % (HOST, PORT, exc))

    log("session-viewer on http://%s:%d" % (HOST, PORT))
    log("first scan: %d sessions in %.0f ms  %s" % (
        len(rows), _scan_ms,
        " ".join("%s=%d" % (s, counts.get(s, 0)) for s in SOURCES)))
    log("token file: %s (mode 600)" % TOKEN_PATH)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
