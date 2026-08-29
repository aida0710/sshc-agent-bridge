import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ManifestTest(unittest.TestCase):
    def test_marketplaces_point_to_the_same_plugin(self):
        codex = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
        claude = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual(codex["name"], "sshc")
        self.assertEqual(claude["name"], "sshc")
        self.assertEqual(codex["plugins"][0]["source"]["path"], "./plugins/sshc-agent-bridge")
        self.assertEqual(claude["plugins"][0]["source"], "./plugins/sshc-agent-bridge")

    def test_hook_commands_are_bundled_and_do_not_use_network(self):
        plugin = ROOT / "plugins/sshc-agent-bridge"
        for path in (plugin / "hooks/hooks.json", plugin / "hooks/claude.json"):
            payload = json.loads(path.read_text())
            for groups in payload["hooks"].values():
                for group in groups:
                    for hook in group["hooks"]:
                        command = hook["command"]
                        self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/report_hook.py", command)
                        self.assertNotIn("curl", command)
                        self.assertNotIn("wget", command)


if __name__ == "__main__":
    unittest.main()
