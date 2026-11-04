# Vasi 统一登录

本 Chart 独立安装；`auth.mode: external` 保留已有部署方接线，`auth.mode: oidc` 消费 Vasi 登记的身份源。
`publicUrl` 必须是包含入口端口的 HTTPS origin，`ingress.host` 必须匹配它。客户端、回调、角色和命名空间由 Vasi 的受管 SSO 操作登记；仅上传 Chart 不会创建身份客户端或授予用户权限。

Vasi 写入专属 CA ConfigMap 和凭据 Secret 的引用；values 只保存引用，不保存密码、客户端密钥或用户令牌。制品发布者必须把所有主容器、初始化容器和 sidecar 镜像纳入完整材料清单并提供已导入 Nexus 的固定摘要。

Chat 的 Element 与 homeserver 使用两个 HTTPS origin：`publicUrl/ingress.host` 与 `homeserverUrl/ingress.homeserverHost`。客户端 ID、issuer、回调均来自 `auth.oidc`；`existingSecret` 提供 `client-secret`，CA 使用 `caConfigMap`。SSO 回跳守门只允许本次配置的完整 origin，包含端口。

`domain` 是 Matrix server_name，首次启动后不可改。Vasi 保存 server_name、issuer 和 client ID 的身份绑定并拒绝重绑定；只改前端入口不能改写已有 Matrix 身份。存量 RocksDB 数据卷必须保留。

IAM 只管理 Chat 的成员使用资格，不暴露尚未实现的 matrix-admin 同步能力。现有 appservice Secret、告警房间和备份准备仍由部署方显式提供；统一登录不会自动创建告警房间。

原生会话与 Keycloak 会话独立。撤销 IAM 成员资格已阻止新的上游登录；默认 upstream 镜像未实现 Keycloak backchannel logout，已有 Matrix 会话按 `accessTokenTtl/refreshTokenTtl` 到期（默认 refresh 最长七天）。本 Chart 不把“重新登录受阻”声明为“所有旧会话立即退出”。需要即时撤销时必须先提供并验收支持会话绑定的受审镜像，不能仅打开 `ssoSessionBinding`。
