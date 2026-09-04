import base64
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTER = ROOT / "plugins" / "sshc-agent-bridge" / "scripts" / "report_hook.py"
SPEC = importlib.util.spec_from_file_location("report_hook", REPORTER)
report_hook = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(report_hook)


def decode_frame(frame: bytes):
    assert frame.startswith(report_hook.OSC_PREFIX)
    assert frame.endswith(report_hook.OSC_SUFFIX)
    encoded = frame[len(report_hook.OSC_PREFIX) : -len(report_hook.OSC_SUFFIX)]
    encoded += b"=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded))


class ReporterTest(unittest.TestCase):
    def test_codex_prompt_becomes_working_without_prompt_or_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            event = report_hook.build_event(
                {
                    "session_id": "thr_123",
                    "cwd": "/workspace",
                    "model": "gpt-5.6-sol",
                    "hook_event_name": "UserPromptSubmit",
                    "prompt": "secret prompt",
                    "transcript_path": "/secret/transcript.jsonl",
                },
                {"PLUGIN_ROOT": "/plugin", "CODEX_HOME": directory},
                42,
            )
        self.assertEqual(
            event,
            {
                "v": 1,
                "agent": "codex",
                "event": "working",
                "session": "thr_123",
                "cwd": "/workspace",
                "model": "gpt-5.6-sol",
                "seq": 42,
            },
        )

    def test_claude_stop_becomes_ready(self):
        event = report_hook.build_event(
            {"session_id": "abc-123", "hook_event_name": "Stop"},
            {"CLAUDE_PLUGIN_ROOT": "/plugin"},
            7,
        )
        self.assertEqual(event["agent"], "claude")
        self.assertEqual(event["event"], "ready")

    def test_codex_uses_the_latest_thread_name_from_the_session_index(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = pathlib.Path(directory) / ".codex"
            codex_home.mkdir()
            index = codex_home / "session_index.jsonl"
            index.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "thr_123", "thread_name": "Old name"}),
                        json.dumps({"id": "another", "thread_name": "Another task"}),
                        json.dumps({"id": "thr_123", "thread_name": "sshc hookを評価する"}),
                    ]
                ),
                encoding="utf-8",
            )

            event = report_hook.build_event(
                {"session_id": "thr_123", "hook_event_name": "Stop"},
                {"SSHC_AGENT_KIND": "codex", "HOME": directory},
                8,
            )

        self.assertEqual(event["name"], "sshc hookを評価する")
        frame = report_hook.encode_osc(event)
        self.assertIsNotNone(frame)
        self.assertEqual(decode_frame(frame)["name"], "sshc hookを評価する")

    def test_codex_session_name_search_is_bounded_to_the_index_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            index = pathlib.Path(directory) / "session_index.jsonl"
            index.write_text(
                json.dumps({"id": "thr_123", "thread_name": "Too old"})
                + "\n"
                + json.dumps({"id": "another", "thread_name": "x" * 160})
                + "\n",
                encoding="utf-8",
            )
            environment = {"SSHC_AGENT_KIND": "codex", "CODEX_HOME": directory}
            with mock.patch.object(report_hook, "MAX_SESSION_INDEX_TAIL_BYTES", 128):
                event = report_hook.build_event(
                    {"session_id": "thr_123", "hook_event_name": "Stop"},
                    environment,
                    9,
                )
                self.assertNotIn("name", event)

                with index.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({"id": "thr_123", "thread_name": "Current"}) + "\n")
                event = report_hook.build_event(
                    {"session_id": "thr_123", "hook_event_name": "Stop"},
                    environment,
                    10,
                )

        self.assertEqual(event["name"], "Current")

    def test_codex_omits_name_when_the_index_is_missing_or_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"SSHC_AGENT_KIND": "codex", "CODEX_HOME": directory}
            missing = report_hook.build_event(
                {"session_id": "thr_123", "hook_event_name": "SessionStart"},
                environment,
                11,
            )
            self.assertNotIn("name", missing)

            (pathlib.Path(directory) / "session_index.jsonl").write_bytes(
                b"not-json\n{\"id\":\"thr_123\",\"thread_name\":7}\n"
            )
            malformed = report_hook.build_event(
                {"session_id": "thr_123", "hook_event_name": "Stop"},
                environment,
                12,
            )

        self.assertNotIn("name", malformed)

    def test_claude_uses_the_newest_ai_title_from_its_own_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = pathlib.Path(directory) / "abc-123.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "ai-title", "aiTitle": "Old name", "sessionId": "abc-123"}),
                        json.dumps({"type": "user", "message": {"content": "secret prompt"}}),
                        json.dumps({"type": "ai-title", "aiTitle": "sshc pluginを直す", "sessionId": "abc-123"}),
                        json.dumps({"type": "assistant", "message": {"content": "secret answer"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            event = report_hook.build_event(
                {
                    "session_id": "abc-123",
                    "cwd": "/workspace",
                    "hook_event_name": "Stop",
                    "transcript_path": str(transcript),
                },
                {"CLAUDE_PLUGIN_ROOT": "/plugin"},
                13,
            )

        self.assertEqual(
            event,
            {
                "v": 1,
                "agent": "claude",
                "event": "ready",
                "session": "abc-123",
                "cwd": "/workspace",
                "name": "sshc pluginを直す",
                "seq": 13,
            },
        )
        self.assertEqual(decode_frame(report_hook.encode_osc(event))["name"], "sshc pluginを直す")

    def test_claude_ignores_a_title_written_for_another_session(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = pathlib.Path(directory) / "resumed.jsonl"
            transcript.write_text(
                json.dumps({"type": "ai-title", "aiTitle": "Previous session", "sessionId": "old-id"})
                + "\n",
                encoding="utf-8",
            )

            event = report_hook.build_event(
                {
                    "session_id": "abc-123",
                    "hook_event_name": "Stop",
                    "transcript_path": str(transcript),
                },
                {"CLAUDE_PLUGIN_ROOT": "/plugin"},
                14,
            )

        self.assertNotIn("name", event)

    def test_claude_session_name_search_is_bounded_to_the_transcript_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = pathlib.Path(directory) / "bounded.jsonl"
            transcript.write_text(
                json.dumps({"type": "ai-title", "aiTitle": "Too old", "sessionId": "abc-123"})
                + "\n"
                + json.dumps({"type": "assistant", "message": {"content": "x" * 200}})
                + "\n",
                encoding="utf-8",
            )
            hook_input = {
                "session_id": "abc-123",
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            }
            with mock.patch.object(report_hook, "MAX_TRANSCRIPT_TAIL_BYTES", 128):
                event = report_hook.build_event(hook_input, {"SSHC_AGENT_KIND": "claude"}, 15)
                self.assertNotIn("name", event)

                with transcript.open("a", encoding="utf-8") as output:
                    output.write(
                        json.dumps({"type": "ai-title", "aiTitle": "Current", "sessionId": "abc-123"})
                        + "\n"
                    )
                event = report_hook.build_event(hook_input, {"SSHC_AGENT_KIND": "claude"}, 16)

        self.assertEqual(event["name"], "Current")

    def test_claude_omits_name_when_the_transcript_is_absent_or_malformed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = report_hook.build_event(
                {
                    "session_id": "abc-123",
                    "hook_event_name": "SessionStart",
                    "transcript_path": str(pathlib.Path(directory) / "not-created.jsonl"),
                },
                {"SSHC_AGENT_KIND": "claude"},
                17,
            )
            self.assertNotIn("name", missing)

            transcript = pathlib.Path(directory) / "malformed.jsonl"
            transcript.write_bytes(
                b"not-json\n"
                b'{"type":"ai-title","aiTitle":7,"sessionId":"abc-123"}\n'
            )
            malformed = report_hook.build_event(
                {
                    "session_id": "abc-123",
                    "hook_event_name": "Stop",
                    "transcript_path": str(transcript),
                },
                {"SSHC_AGENT_KIND": "claude"},
                18,
            )
            self.assertNotIn("name", malformed)

        without_path = report_hook.build_event(
            {"session_id": "abc-123", "hook_event_name": "Stop", "transcript_path": 7},
            {"SSHC_AGENT_KIND": "claude"},
            19,
        )
        self.assertNotIn("name", without_path)

    def test_unknown_hook_is_ignored(self):
        self.assertIsNone(
            report_hook.build_event(
                {"session_id": "abc-123", "hook_event_name": "PreToolUse"},
                {},
                1,
            )
        )

    def test_pts_path_decodes_the_linux_device_number(self):
        self.assertEqual(report_hook._pts_path(0x8800), "/dev/pts/0")
        self.assertEqual(report_hook._pts_path(0x8801), "/dev/pts/1")
        self.assertEqual(report_hook._pts_path(0x108800), "/dev/pts/256")
        self.assertEqual(report_hook._pts_path(0), "")
        self.assertEqual(report_hook._pts_path(0x400), "")

    def test_terminal_candidates_try_dev_tty_then_ancestry_then_ssh_tty(self):
        with mock.patch.object(report_hook, "_ancestor_terminals", lambda: ["/dev/pts/7"]):
            self.assertEqual(
                report_hook._terminal_candidates({"SSH_TTY": "/dev/pts/9"}),
                ["/dev/tty", "/dev/pts/7", "/dev/pts/9"],
            )
            self.assertEqual(
                report_hook._terminal_candidates({"SSH_TTY": "/dev/pts/7"}),
                ["/dev/tty", "/dev/pts/7"],
            )
            self.assertEqual(report_hook._terminal_candidates({}), ["/dev/tty", "/dev/pts/7"])

    def test_only_a_terminal_device_under_dev_is_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            ordinary = pathlib.Path(directory) / "not-a-terminal"
            ordinary.write_text("", encoding="utf-8")
            self.assertIsNone(report_hook._open_terminal(str(ordinary)))
        self.assertIsNone(report_hook._open_terminal("/dev/../etc/passwd"))
        self.assertIsNone(report_hook._open_terminal("/dev/null"))

        primary, secondary = os.openpty()
        try:
            descriptor = report_hook._open_terminal(os.ttyname(secondary))
            self.assertIsNotNone(descriptor)
            os.close(descriptor)
        finally:
            os.close(primary)
            os.close(secondary)

    def test_the_frame_reaches_the_pane_terminal_past_an_unusable_candidate(self):
        primary, secondary = os.openpty()
        try:
            frame = report_hook.encode_osc({"v": 1, "agent": "claude", "event": "ready", "seq": 1})
            # An agent that starts hooks in a new session leaves /dev/tty
            # unopenable. The reporter must skip it and reach the pane instead.
            candidates = ["/dev/null", os.ttyname(secondary)]
            with mock.patch.object(report_hook, "_terminal_candidates", lambda environment: candidates):
                self.assertTrue(report_hook.write_to_tty(frame, {}))
            self.assertEqual(os.read(primary, len(frame) + 16), frame)
        finally:
            os.close(primary)
            os.close(secondary)

    def test_write_reports_failure_when_no_candidate_is_a_terminal(self):
        with mock.patch.object(report_hook, "_terminal_candidates", lambda environment: ["/dev/null"]):
            self.assertFalse(report_hook.write_to_tty(b"frame", {}))

    def test_osc_round_trip_uses_base64url(self):
        event = {
            "v": 1,
            "agent": "claude",
            "event": "attention",
            "session": "abc-123",
            "seq": 9,
        }
        frame = report_hook.encode_osc(event)
        self.assertIsNotNone(frame)
        self.assertEqual(decode_frame(frame), event)
        self.assertNotIn(b"=", frame)

    def test_payload_is_bounded(self):
        self.assertIsNone(report_hook.encode_osc({"value": "x" * 5000}))

    def test_encoded_payload_matches_the_sshc_decoder_limit(self):
        frame = report_hook.encode_osc({"value": "x" * 3000})
        self.assertIsNotNone(frame)
        encoded = frame[len(report_hook.OSC_PREFIX) : -len(report_hook.OSC_SUFFIX)]
        self.assertLessEqual(len(encoded), report_hook.MAX_ENCODED_PAYLOAD_BYTES)


if __name__ == "__main__":
    unittest.main()
