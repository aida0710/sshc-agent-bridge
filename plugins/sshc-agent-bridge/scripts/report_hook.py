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


def write_to_tty(frame: bytes) -> bool:
    flags = os.O_WRONLY | getattr(os, "O_NOCTTY", 0)
    try:
        descriptor = os.open("/dev/tty", flags)
    except OSError:
        return False
    try:
        if not os.isatty(descriptor):
            return False
        view = memoryview(frame)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                return False
            view = view[written:]
        return True
    except OSError:
        return False
    finally:
        os.close(descriptor)


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
