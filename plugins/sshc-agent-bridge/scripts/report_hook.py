#!/usr/bin/env python3
"""Translate Claude Code or Codex hook input into sshc's private OSC event."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from typing import Any, Mapping

MAX_INPUT_BYTES = 1 << 20
MAX_ENCODED_PAYLOAD_BYTES = 4096
MAX_SESSION_INDEX_TAIL_BYTES = 1 << 20
MAX_TRANSCRIPT_TAIL_BYTES = 1 << 20
# An agent may put its hooks a few processes away from the pane. Walking a
# bounded ancestry keeps the search finite on an unexpected process tree.
MAX_TERMINAL_ANCESTRY = 8
LINUX_PTS_MAJOR = 136
OSC_PREFIX = b"\x1b]6973;"
OSC_SUFFIX = b"\x1b\\"

STATE_BY_HOOK = {
    "SessionStart": "ready",
    "UserPromptSubmit": "working",
    "PermissionRequest": "attention",
    "Notification": "attention",
    "Elicitation": "attention",
    "Stop": "ready",
    "StopFailure": "attention",
    "SessionEnd": "ended",
}


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(
        character for character in value if character >= " " and character != "\x7f"
    )[:limit].strip()


def _agent_from_environment(environment: Mapping[str, str]) -> str:
    explicit = environment.get("SSHC_AGENT_KIND", "")
    if explicit in {"claude", "codex"}:
        return explicit
    return "codex" if environment.get("PLUGIN_ROOT") else "claude"


def _codex_home(environment: Mapping[str, str]) -> str:
    configured = environment.get("CODEX_HOME")
    if configured:
        return configured
    home = environment.get("HOME")
    if home:
        return os.path.join(home, ".codex")
    return os.path.expanduser("~/.codex")


def _tail_lines(path: str, limit: int) -> list[bytes]:
    """Return whole lines from at most the last limit bytes, oldest first.

    A bounded tail keeps the reporter's cost independent of how long the agent
    has been running, and never loads a whole transcript into memory.
    """
    try:
        with open(path, "rb") as source:
            end = source.seek(0, os.SEEK_END)
            start = max(0, end - limit)
            starts_mid_line = False
            if start > 0:
                source.seek(start - 1)
                starts_mid_line = source.read(1) != b"\n"
            source.seek(start)
            data = source.read(end - start)
    except (OSError, ValueError):
        return []

    if starts_mid_line:
        _, separator, data = data.partition(b"\n")
        if not separator:
            return []
    return data.splitlines()


def _json_object(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def _codex_session_name(session: str, environment: Mapping[str, str]) -> str:
    index_path = os.path.join(_codex_home(environment), "session_index.jsonl")
    for line in reversed(_tail_lines(index_path, MAX_SESSION_INDEX_TAIL_BYTES)):
        record = _json_object(line)
        if record is None or record.get("id") != session:
            continue
        return _text(record.get("thread_name"), 160)
    return ""


def _claude_session_name(session: str, hook_input: Mapping[str, Any]) -> str:
    """Read the newest generated title out of this session's own transcript.

    Claude Code appends an `ai-title` record whenever it regenerates the title,
    so the newest matching record from the tail is the current name. The
    transcript path is used to open the file and is never copied into the event.
    """
    transcript_path = hook_input.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""
    for line in reversed(_tail_lines(transcript_path, MAX_TRANSCRIPT_TAIL_BYTES)):
        record = _json_object(line)
        if record is None or record.get("type") != "ai-title":
            continue
        if record.get("sessionId") != session:
            continue
        return _text(record.get("aiTitle"), 160)
    return ""


def build_event(
    hook_input: Mapping[str, Any],
    environment: Mapping[str, str],
    sequence: int,
) -> dict[str, Any] | None:
    hook_name = hook_input.get("hook_event_name")
    state = STATE_BY_HOOK.get(hook_name)
    session = _text(hook_input.get("session_id"), 256)
    if state is None or not session or sequence <= 0:
        return None

    agent = _agent_from_environment(environment)
    event: dict[str, Any] = {
        "v": 1,
        "agent": agent,
        "event": state,
        "session": session,
        "seq": sequence,
    }
    cwd = _text(hook_input.get("cwd"), 160)
    model = _text(hook_input.get("model"), 160)
    if cwd:
        event["cwd"] = cwd
    if model:
        event["model"] = model
    if agent == "codex":
        name = _codex_session_name(session, environment)
    else:
        name = _claude_session_name(session, hook_input)
    if name:
        event["name"] = name
    return event


def encode_osc(event: Mapping[str, Any]) -> bytes | None:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    if len(encoded) > MAX_ENCODED_PAYLOAD_BYTES:
        return None
    return OSC_PREFIX + encoded + OSC_SUFFIX


def _pts_path(device: int) -> str:
    """Turn a Linux tty device number into its /dev/pts path."""
    if device <= 0:
        return ""
    major = (device >> 8) & 0xFFF
    if major != LINUX_PTS_MAJOR:
        return ""
    minor = (device & 0xFF) | ((device >> 12) & 0xFFF00)
    return f"/dev/pts/{minor}"


def _ancestor_terminals() -> list[str]:
    """Controlling terminals of this process's ancestors, nearest first.

    An agent that starts its hooks in a new session leaves them without a
    controlling terminal, so `/dev/tty` cannot be opened. The agent process
    itself still owns the pane's terminal. Only Linux is covered here; other
    platforms fall back to the login session's SSH_TTY.
    """
    found: list[str] = []
    pid = os.getppid()
    for _ in range(MAX_TERMINAL_ANCESTRY):
        if pid <= 1:
            break
        try:
            with open(f"/proc/{pid}/stat", "rb") as stat:
                data = stat.read()
        except OSError:
            break
        # The second field is the executable name in parentheses and may itself
        # contain spaces or parentheses, so parse the fixed fields after it.
        close = data.rfind(b")")
        fields = data[close + 2 :].split() if close >= 0 else []
        if len(fields) < 5:
            break
        try:
            parent, device = int(fields[1]), int(fields[4])
        except ValueError:
            break
        path = _pts_path(device)
        if path and path not in found:
            found.append(path)
        pid = parent
    return found


def _terminal_candidates(environment: Mapping[str, str]) -> list[str]:
    """Ordered write targets for the pane that is displaying this agent."""
    candidates = ["/dev/tty"]
    for path in _ancestor_terminals():
        if path not in candidates:
            candidates.append(path)
    ssh_tty = environment.get("SSH_TTY", "")
    if ssh_tty and ssh_tty not in candidates:
        candidates.append(ssh_tty)
    return candidates


def _open_terminal(path: str) -> int | None:
    """Open path only when it is a real terminal device under /dev."""
    if not path.startswith("/dev/") or os.path.normpath(path) != path:
        return None
    flags = os.O_WRONLY | getattr(os, "O_NOCTTY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        if not os.isatty(descriptor):
            os.close(descriptor)
            return None
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def write_to_tty(frame: bytes, environment: Mapping[str, str] | None = None) -> bool:
    resolved = os.environ if environment is None else environment
    for path in _terminal_candidates(resolved):
        descriptor = _open_terminal(path)
        if descriptor is None:
            continue
        started = False
        try:
            view = memoryview(frame)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    return False
                started = True
                view = view[written:]
            return True
        except OSError:
            # A partly written frame must not be repeated on another terminal.
            if started:
                return False
        finally:
            os.close(descriptor)
    return False


def read_hook_input(stream: Any) -> Mapping[str, Any] | None:
    data = stream.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        return None
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def main() -> int:
    hook_input = read_hook_input(sys.stdin.buffer)
    if hook_input is not None:
        event = build_event(hook_input, os.environ, time.monotonic_ns())
        if event is not None:
            frame = encode_osc(event)
            if frame is not None:
                write_to_tty(frame)

    # Codex Stop hooks require JSON on stdout. An empty object has no effect in
    # Claude Code and keeps this bridge advisory in both products.
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
