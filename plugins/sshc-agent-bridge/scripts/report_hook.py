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


def _codex_session_name(session: str, environment: Mapping[str, str]) -> str:
    index_path = os.path.join(_codex_home(environment), "session_index.jsonl")
    try:
        with open(index_path, "rb") as index:
            end = index.seek(0, os.SEEK_END)
            start = max(0, end - MAX_SESSION_INDEX_TAIL_BYTES)
            starts_mid_line = False
            if start > 0:
                index.seek(start - 1)
                starts_mid_line = index.read(1) != b"\n"
            index.seek(start)
            data = index.read(end - start)
    except (OSError, ValueError):
        return ""

    if starts_mid_line:
        _, separator, data = data.partition(b"\n")
        if not separator:
            return ""

    for line in reversed(data.splitlines()):
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("id") != session:
            continue
        return _text(record.get("thread_name"), 160)
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
