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

    event: dict[str, Any] = {
        "v": 1,
        "agent": _agent_from_environment(environment),
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
