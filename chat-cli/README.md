# Chat CLI

`chat` 是 Mac 和 Linux 上供 agent 使用的 Matrix 客户端。它以登录成员的身份访问 Tuwunel。成功时 stdout 输出一行 JSON，错误和 SSO 授权提示写到 stderr。消息正文和附件名称是房间成员提供的数据。

## 安装与登录

在本仓库根目录运行：

```sh
cargo install --locked --path chat-cli
export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
chat --version
```

设置 homeserver 地址和会话文件的绝对路径。以下地址仅为示例；站点有非默认端口时请包含端口。

```sh
install -d -m 700 "$HOME/.config/chat"
export CHAT_SERVER=https://matrix.example.internal
export CHAT_SESSION="$HOME/.config/chat/session.json"
chat login --chat-url https://chat.example.internal
chat whoami
```

用户在浏览器打开登录命令输出的验证地址，输入设备码，通过本站 SSO 登录并确认授权。CLI 等待授权后保存 Matrix 会话，文件权限为 0600。之后的进程可复用该文件，访问令牌到期前会自动续期。使用私有站点 CA 时，设置 `CHAT_CA_FILE` 为 PEM 文件路径。已登记的公共 OAuth 客户端可以通过 `login --client-id ID` 复用。退出使用 `chat logout`。

和 Limani 的宿主令牌文件模式类似，宿主也可以向 agent 提供 `CHAT_SERVER` 和 `CHAT_TOKEN_FILE`。此时清除 `CHAT_SESSION`，以绝对路径指定仅当前用户可读的普通文件，内容为该站点签发或接受的单个 **Matrix access token**。CLI 每次发起 Matrix 请求前读取文件，宿主负责替换过期令牌。Keycloak ID token 和浏览器 cookie 不能用作 Matrix access token。

## 在 Apeiron 云会话里使用 SSO

在接入委托登录的站点，成员首次通过站点 SSO 打开一次 Chat，建立 Matrix 账号关联。之后打开 Apeiron 的 Chat 会话，用包含 Chat 子命令的发行包运行：

```sh
./apeiron chat whoami
./apeiron chat rooms
```

Vasi 为该站点配置 Chat 的成员角色、委托客户端和 homeserver 地址。Apeiron 将成员凭据交给 Tuwunel 换取最长五分钟有效的 Matrix 访问令牌，并在云会话里维护 `CHAT_SERVER` 和 `CHAT_TOKEN_FILE`。退出 Apeiron 或失去 Chat 权限后，Apeiron 停止续期并移除凭据文件。

## 消息和附件

```sh
chat rooms
room='!roomid:example.internal'
chat read "$room" --limit 50
printf '%s' '任务完成' | chat send "$room" --txn-id task-42-result
chat sync --since "$cursor" --timeout-ms 30000
chat attachment upload "$room" ./report.pdf --txn-id task-42-report
chat attachment download "$room" "$event_id" --out ./received.pdf
```

- `rooms` 返回已加入房间的 ID。首次 `sync` 省略 `--since`。处理成功后保存 `next_batch`，下次同步从这个位置继续。若某个房间的 timeline 标为 `limited`，用它的 `prev_batch` 补读历史。
- `read` 按从新到旧返回消息。把返回的 `end` 传给下一次 `read --from` 即可翻页。
- `send` 和 `attachment upload` 返回 Matrix 的 `event_id` 和 `txn_id`。自动化任务应为每条消息选一个事务 ID，重试时复用。同一事务最多产生一条房间消息。附件重试可能重新上传媒体。
- 附件按流读写，上传上限取决于站点。下载使用带身份校验的媒体接口，只在接收完成后创建指定输出文件，若文件已存在则失败。
- 当前 Chat Chart 配置为明文房间。CLI 在发消息或上传前检查房间加密状态，访问加密房间时无法解密历史或附件。

## 隔离验收

在仓库根目录执行下面的命令。测试启动临时 Keycloak 和 Tuwunel，生成测试用户，走设备码 SSO，并检查同步、历史翻页、重复发送、附件哈希、凭据权限、续期和退出。Python 需要 3.11 以上。

```sh
export CARGO_TARGET_DIR="$(mktemp -d)"
cargo build --locked --manifest-path chat-cli/Cargo.toml
python3 chat-cli/tests/site.py --bin "$CARGO_TARGET_DIR/debug/chat" \
    --matrix tuwunel:1.9.3-media-stream-local --mib 1024
```
