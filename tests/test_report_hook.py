import base64
import importlib.util
import json
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

    def test_unknown_hook_is_ignored(self):
        self.assertIsNone(
            report_hook.build_event(
                {"session_id": "abc-123", "hook_event_name": "PreToolUse"},
                {},
                1,
            )
        )

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
