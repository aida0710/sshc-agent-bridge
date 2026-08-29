# sshc Agent Bridge

Claude Code、Codex、OpenCodeのsession状態を、そのagentを実行している
[sshc](https://github.com/aida0710/sshc) Terminal paneへ報告する小さなplugin集です。

> [!IMPORTANT]
> 現在はexperimentalです。sshc側はAgent Bridge protocolを含む開発版が必要です。
> 公開済みのsshc v0.18.0では、受信した状態はまだUIに表示されません。

## 何が変わるか

sshcのTerminalでagentを実行すると、pane headerに次の状態を表示できるようになります。

- `working`: agentが応答を生成中
- `attention`: permissionや入力を待機中
- `ready`: 応答が完了し、次の入力を待機中
- `ended`: native sessionを終了。sshc側では再開候補として保持

OpenCodeではsession titleも報告します。Claude CodeとCodexの公式hookには安定した
session title fieldがないため、現時点ではsshcのpane titleまたは接続aliasを使います。

## インストール

### Codex

```text
codex plugin marketplace add aida0710/sshc-agent-bridge
codex plugin add sshc-agent-bridge@sshc
```

新しいCodex sessionを開始し、`/hooks`でplugin hookの内容を確認して信頼してください。
hookが更新されるとhashが変わるため、Codexは再確認するまで新しいhookを実行しません。

### Claude Code

Claude Code内で実行します。

```text
/plugin marketplace add aida0710/sshc-agent-bridge
/plugin install sshc-agent-bridge@sshc
```

pluginを有効化した後、新しいsessionを開始してください。

### OpenCode

OpenCode adapterはnpm公開前です。現時点ではrepositoryをcloneし、`opencode.json`の
`plugin`へ絶対pathを追加します。

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["file:///absolute/path/to/sshc-agent-bridge/opencode/index.js"]
}
```

npm package `sshc-agent-bridge-opencode`の公開後は、このlocal pathをpackage名へ
置き換えられる設計です。

## Privacy / Security

このbridgeは次の情報だけをsshcへ渡します。

- agent種別
- native session ID
- lifecycle状態
- working directory
- model ID（公式hookが提供する場合）
- session title（OpenCodeが提供する場合）

prompt、assistant response、tool input/output、transcript本文は送信しません。hook inputの
該当fieldは無視し、network request、設定fileの書き換え、agentの許可判断の変更も
行いません。出力先は現在の`/dev/tty`だけです。sshc以外のterminalでは未知のOSCが
無視され、TTYがない環境では何もせず成功終了します。

詳細は[SECURITY.md](SECURITY.md)を参照してください。

## Repository layout

```text
.agents/plugins/marketplace.json           Codex marketplace
.claude-plugin/marketplace.json            Claude Code marketplace
plugins/sshc-agent-bridge/
  .codex-plugin/plugin.json
  .claude-plugin/plugin.json
  hooks/hooks.json                          Codex lifecycle hooks
  hooks/claude.json                         Claude Code lifecycle hooks
  scripts/report_hook.py                    shared OSC reporter
opencode/                                   OpenCode package
tests/                                      contract tests
```

## Development

Python 3.10以上とNode.js 18以上を使用します。

```sh
python3 -m unittest discover -s tests -v
cd opencode && npm test
```

Codex manifestはOpenAIの`plugin-creator` validatorでも検証しています。

## Protocol

Bridgeはcompact JSONをbase64url encodingし、次のprivate OSCとして`/dev/tty`へ
書き込みます。

```text
ESC ] 6973 ; <base64url JSON> ST
```

payloadは4 KiB以下、versionは`1`です。このeventはsshc内で表示・通知・利用者が
明示したnative session再開だけに使い、接続identityや任意command実行のauthorityには
使いません。

## License

MIT
