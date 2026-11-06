# Vasi 受管统一登录契约

当前候选要求 `auth.mode: oidc`，由 Vasi 配发专属 client、凭据和公共 CA 引用。
`publicUrl/ingress.host`（Element）与 `homeserverUrl/ingress.homeserverHost`（homeserver）
必须为本站不同的 HTTPS origin，包含入口非默认端口。公开 values 可在创建 Secret 前生成并导入检查。

客户端 ID 为 `vasi-<namespace>-<release>`，回调为
`<homeserverUrl>/_matrix/client/unstable/login/sso/callback/<clientId>`。
`auth.oidc.existingSecret` 只提供 `client-secret`，`caConfigMap` 提供 `ca.crt`。
凭据、kubeconfig 和用户 token 不进入普通 values；不允许外站 SSO 回跳或未经验证的 backchannel。

`domain` 是持久 Matrix server_name，Vasi 及 Chart 的身份标记核对 server_name、issuer、client ID；
有旧 PVC 而无可信身份标记时拒绝接管。0.2.0 外部接线模式或旧 matrix-app 不自动迁移。
安装不是成员授权：`matrix-user` 才能新登录，管理员同步还需 `matrix-admin` 和已存在的 Matrix 用户。

告警房间由 namespace-local 初始化器创建/核验；默认不需要 IAM 管理员凭据。
可选管理员同步须显式准备只读目录客户端，默认关闭；详见 README。

原生 Matrix 会话与 Keycloak 会话独立。无角色用户的新登录拒绝不等于旧会话立即销毁。
默认 access/refresh TTL 为 1h/7d；全会话撤销与备份恢复须单独验收。
