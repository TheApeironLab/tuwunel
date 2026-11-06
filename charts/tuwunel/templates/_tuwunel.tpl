{{- define "matrix.tuwunelToml" -}}
{{- $d := required "domain (SITE_DOMAIN) is required" .Values.domain -}}
[global]
{{- if .Values.ssoSessionBinding }}{{ fail "Session binding requires a separately reviewed image and integration" }}{{ end }}
# server_name 是用户 ID 的后缀（@alice:{{ $d }}），首次启动后写进数据库，之后换域名 Tuwunel
# 直接拒绝启动（"Database belongs to ..."）。换域名 = 清空聊天数据，见 ADR-0016。
server_name = {{ $d | quote }}
database_path = "/var/lib/tuwunel/db"
database_backup_path = "/var/lib/tuwunel/backups"
database_backups_to_keep = {{ .Values.backupsToKeep }}
address = ["0.0.0.0"]
port = 8008
new_user_displayname_suffix = ""
log = "info"

# 离线内网：不联邦、不信任任何外部密钥服务器、不上报
allow_federation = false
trusted_servers = []
sentry = false

# 账号只来自 Keycloak（SSO 首登自动建号）；没有密码登录、开放注册、注册令牌和访客
allow_registration = false
allow_guest_registration = false
login_with_password = false
login_via_existing_session = false
grant_admin_to_first_user = false
federate_admin_room = false

# 平台统一不加密（用户裁决，审计与机器人需要明文）：服务端拒绝 m.room.encryption
# 状态事件与 m.room.encrypted 消息，建房请求里的加密初始状态被丢弃
allow_encryption = false
encryption_enabled_by_default_for_room_type = "none"

allow_legacy_media = false
max_request_size = {{ .Values.maxUploadSize | quote }}

# 资源：RocksDB 缓存与线程池按宿主核数扩张，压住它们才能待在 memory limit 里
cache_capacity_modifier = {{ .Values.cacheCapacityModifier }}
db_pool_max_workers = {{ .Values.dbPoolMaxWorkers }}
rocksdb_allow_fallocate = {{ .Values.rocksdbAllowFallocate }}

# 会话：access 1 小时；refresh 7 天且不滑动，到期必须经 Keycloak 重新登录（停用的账号最多 7 天失效）
access_token_ttl = {{ .Values.accessTokenTtl }}
refresh_token_ttl = {{ .Values.refreshTokenTtl }}
refresh_token_idle_only = false

# 内置 OIDC 授权服务器（MSC3861）只做中转：不提供本地账号登录页，直接跳上游 Keycloak；
# 只有 chat.<domain> 上的 Element 能免审批注册为客户端
oidc_native_auth = false
oidc_registration_allowed_redirect_hosts = [{{ default (printf "chat.%s" $d) .Values.ingress.host | quote }}]
oidc_aware_preferred = true

# backup.sh 经 sidecar 发 SIGUSR2 触发在线备份（镜像里没有 shell，不能 exec 进主容器）
admin_signal_execute = ["server backup-database"]

# 管理员与 Keycloak realm role matrix-admin 同步（N2 2026-10-03，MSG-2）：同步 bot @admin-sync 每次启动被设为管理员
# （幂等），configure.sh finalize 用它的令牌经 /_synapse/admin 授予/撤销。管理员命令不能在 #admins 之外的房间转义执行
# （否则命令与回显公开在那个房间里）。
{{- if .Values.adminSync.enabled }}
admin_execute = [{{ printf "users make-user-admin @admin-sync:%s" $d | quote }}]
{{- end }}
admin_escape_commands = false

[global.well_known]
# 也是内置 OIDC 授权服务器的 issuer
client = "{{ include "chat.homeserver" . }}"

[[global.identity_provider]]
brand = "Keycloak"
# client_id 与 issuer_url 是 Tuwunel 绑定身份的键，(client_id, sub) 一旦写进数据库就永远不能改
client_id = {{ include "chat.clientId" . | quote }}
client_secret_file = "/etc/tuwunel/oidc/client-secret"
issuer_url = {{ include "chat.issuer" . | quote }}
callback_url = {{ default (printf "%s/_matrix/client/unstable/login/sso/callback/%s" (include "chat.homeserver" .) (include "chat.clientId" .)) .Values.auth.oidc.callbackUrl | quote }}
# localpart 取 Keycloak username（用户不能自改）；不合法或被占用时报错，不生成随机 ID
userid_claims = ["preferred_username"]
unique_id_fallbacks = false
# 不按同名认领已有账号：Keycloak 删了重建同名用户（sub 变了）会被拒，防接管
trusted = false
registration = true
default = true

# 告警 bot：appservice 的 as_token 就是 @alertbot 的访问令牌，不需要密码登录。
# as_token / hs_token 经环境变量 TUWUNEL_APPSERVICE__ALERTS__* 从 Secret matrix-appservice 注入。
{{- if .Values.alerts.enabled }}
[global.appservice.alerts]
sender_localpart = "alertbot"

[[global.appservice.alerts.users]]
exclusive = true
regex = {{ printf "^@alertbot:%s$" (regexQuoteMeta $d) | quote }}

# appservice 只能在自己的命名空间里建别名；独占也防止别人抢注 #alerts
[[global.appservice.alerts.aliases]]
exclusive = true
regex = {{ printf "^#alerts:%s$" (regexQuoteMeta $d) | quote }}

# 管理员同步 bot（N2 2026-10-03，MSG-2）：只用来调 /_synapse/admin 授予/撤销管理员、清理测试媒体；令牌在 Secret matrix-admin-appservice。
{{- end }}
{{- if .Values.adminSync.enabled }}
[global.appservice.admin]
sender_localpart = "admin-sync"

[[global.appservice.admin.users]]
exclusive = true
regex = {{ printf "^@admin-sync:%s$" (regexQuoteMeta $d) | quote }}
{{- end }}
{{- if .Values.e2eAppservice }}

# 仅测试环境（values e2eAppservice=true）：tests/e2e 用它注册/扮演 @e2e-* 用户。令牌来自 Secret matrix-e2e-appservice。
[global.appservice.e2e]
sender_localpart = "e2e-bot"

[[global.appservice.e2e.users]]
exclusive = true
regex = {{ printf "^@e2e-[a-z0-9._=-]+:%s$" (regexQuoteMeta $d) | quote }}
{{- end }}
{{- end -}}
