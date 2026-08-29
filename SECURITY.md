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
- OpenCodeのroot session `title`
- 正規化済みlifecycle state

次のfieldはcopy、保存、送信しません。

- promptとassistant response
- transcript内容とtranscript path
- tool input/output
- permission理由
- credentialsとenvironment全体

Reporterはnetwork APIを呼ばず、永続fileも作りません。唯一のwrite先は`/dev/tty`です。
hook inputは最大1 MiB、OSC JSON payloadは最大4 KiBに制限します。例外、TTY不在、未知の
eventはagentの動作を止めずに無視します。

## Supported environment

Lifecycle reporterは`/dev/tty`を持つLinuxまたはmacOS sessionを対象とします。
Windows native sessionは現時点では未対応です。WSL内のPTYはUnix環境として動作します。

## Reporting a vulnerability

公開issueへ秘密情報を貼らず、GitHubのprivate vulnerability reportingを使用してください。
