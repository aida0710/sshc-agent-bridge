# sshc Agent Bridge

Claude Code、Codex、OpenCodeのセッション状態を、各エージェントを実行している
[sshc](https://github.com/aida0710/sshc)のTerminalペインへ通知するための小さなプラグイン集です。

> [!IMPORTANT]
> 現在は実験的な機能です。Agent Bridgeプロトコルを含むsshc v0.19.0以上が必要です。

## できること

sshcのTerminalでエージェントを実行すると、ペインヘッダーに次の状態を表示できるようになります。

- `working`: エージェントが応答を生成中
- `attention`: 権限確認やユーザー入力を待機中
- `ready`: 応答が完了し、次の入力を待機中
- `ended`: ネイティブセッションが終了。sshc側では再開候補として保持

OpenCodeでは、公式イベントからセッションタイトルを取得して報告します。Codexでは、公式フックの`session_id`と
ローカルの`~/.codex/session_index.jsonl`を照合し、最新の`thread_name`があれば報告します。
Claude Codeでは、公式フックの`transcript_path`が指すトランスクリプトを末尾から読み取り、同じ`session_id`を持つ
最新の`ai-title`レコードの`aiTitle`があれば報告します。

いずれも`SessionStart`の時点ではまだ生成されていないことがあり、その場合は省略され、次回の`Stop`または
`UserPromptSubmit`で反映されます。セッション名を解決できない場合は、sshcのペインタイトルまたは接続エイリアスを使用します。

## インストール

### Codex

```text
codex plugin marketplace add aida0710/sshc-agent-bridge
codex plugin add sshc-agent-bridge@sshc
```

新しいCodexセッションを開始し、`/hooks`でプラグインフックの内容を確認して信頼してください。
フックが更新されるとハッシュが変わるため、Codexは再確認されるまで新しいフックを実行しません。

### Claude Code

Claude Code内で次のコマンドを実行します。

```text
/plugin marketplace add aida0710/sshc-agent-bridge
/plugin install sshc-agent-bridge@sshc
```

プラグインを有効化した後、新しいセッションを開始してください。

### OpenCode

OpenCode向けアダプターは、まだnpmに公開していません。現時点ではリポジトリをcloneし、`opencode.json`の
`plugin`に絶対パスを追加します。

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["file:///absolute/path/to/sshc-agent-bridge/opencode/index.js"]
}
```

npmパッケージ`sshc-agent-bridge-opencode`の公開後は、このローカルパスをパッケージ名に
置き換えて利用できる予定です。

## プライバシー / セキュリティ

このBridgeがsshcへ渡す情報は、次のものだけです。

- エージェント種別
- ネイティブセッションID
- ライフサイクル状態
- 作業ディレクトリ
- モデルID（公式フックから取得できる場合）
- セッションタイトル（OpenCodeの公式イベント、Codexのローカルセッションインデックス、またはClaude Codeのトランスクリプトから取得できる場合）

プロンプト、アシスタントの応答、ツールの入出力、トランスクリプトのパスと本文は送信しません。

セッション名の解決のためだけに、Codexでは`session_index.jsonl`を、Claude Codeでは自身のトランスクリプトを
それぞれ末尾最大1 MiBだけ読み取ります。該当セッションの`thread_name`と`aiTitle`以外の情報はOSCへ含めません。

ネットワーク通信、設定ファイルの書き換え、エージェントの許可判断の変更も行いません。
OSCの出力先は現在の`/dev/tty`だけです。sshc以外のターミナルでは未知のOSCは無視され、
TTYがない環境では何もせず正常終了します。

詳細は[SECURITY.md](SECURITY.md)を参照してください。

## リポジトリ構成

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

## 開発

Python 3.10以上とNode.js 18以上を使用します。

```sh
python3 -m unittest discover -s tests -v
cd opencode && npm test
```

Codexのmanifestは、OpenAIの`plugin-creator` validatorでも検証しています。

## プロトコル

BridgeはコンパクトなJSONをbase64urlでエンコードし、次のプライベートOSCとして`/dev/tty`へ
書き込みます。

```text
ESC ] 6973 ; <base64url JSON> ST
```

ペイロードは4 KiB以下で、バージョンは`1`です。

このイベントは、sshc内での表示・通知と、ユーザーが明示的に指示したネイティブセッションの再開にのみ使用します。
接続identityの根拠や、任意コマンドを実行するためのauthorityとしては使用しません。

## License

MIT
