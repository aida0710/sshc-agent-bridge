# Security policy

## Threat model

sshc Agent Bridgeは、agent自身と同じremote account・同じPTYで動くadvisoryな
integrationです。agent hook eventを認証済みのremote identityとして扱いません。
受信側のsshcも、このeventを表示、通知、固定形式のnative session再開候補にだけ使います。

## Data handling

BridgeがOSC payloadへcopyするfieldはallowlistで固定しています。

- `session_id`
- `cwd`
- `model`
- Codexのローカルsession indexにある該当sessionの`thread_name`
- Claude Codeのtranscriptにある該当sessionの`aiTitle`
- OpenCodeのroot session `title`
- 正規化済みlifecycle state

次のfieldはcopy、保存、送信しません。

- promptとassistant response
- transcript path
- transcript本文（session名として選ばれた`aiTitle`を除く）
- tool input/output
- permission理由
- credentialsとenvironment全体

Reporterはnetwork APIを呼ばず、永続fileも作りません。write先は端末deviceだけです。
まず`/dev/tty`を試し、開けない場合は祖先processの制御端末、続いて`SSH_TTY`を試します。
候補は`/dev/`配下の正規化済みpathに限り、`os.isatty`が真のfile descriptorへだけ書き込みます。
Claude Codeはhookを新しいsessionで起動するため`/dev/tty`が`ENXIO`で開けず、この順序が必要です。
祖先の制御端末はLinuxの`/proc/<pid>/stat`から最大8世代だけ辿ります。
Codexでは`CODEX_HOME/session_index.jsonl`（既定は`~/.codex/session_index.jsonl`）の末尾
最大1 MiBを読み、hookの`session_id`と一致する最新行の`thread_name`だけを利用します。
Claude Codeではhookが渡す`transcript_path`のfileの末尾最大1 MiBを読み、hookの`session_id`と
一致する最新の`ai-title` recordの`aiTitle`だけを利用します。transcriptのそれ以外のrecordは
読み捨て、`transcript_path`自体もOSCへ含めません。
fileがない、形式が変わった、名前がまだ生成されていない場合は名前を省略します。hook
inputは最大1 MiB、OSC JSON payloadは最大4 KiBに制限します。例外、TTY不在、未知のeventは
agentの動作を止めずに無視します。

## Supported environment

Lifecycle reporterはLinuxまたはmacOSのsessionを対象とします。macOSでは祖先process
からの解決を行わないため、`/dev/tty`も`SSH_TTY`も使えない構成では報告を省略します。
Windows native sessionは現時点では未対応です。WSL内のPTYはUnix環境として動作します。

## Reporting a vulnerability

公開issueへ秘密情報を貼らず、GitHubのprivate vulnerability reportingを使用してください。
