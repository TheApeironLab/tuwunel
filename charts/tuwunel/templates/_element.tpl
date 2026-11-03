{{- /*
Element Web 的 config.json 与根域 /.well-known/matrix/client。
自托管必须改掉的默认值（ADR-0016）：上游 config.sample.json
指向 vector.im、scalar、meet.element.io、maptiler、matrix.org。这里一项不留：
- 不写 posthog / sentry / bug_report_endpoint_url（不写即不启用遥测与 rageshake，element docs/config.md）
- 身份服务器、集成管理器、Jitsi、Element Call、地图、链接预览、Widget 全部关掉
- 只允许平台 homeserver，注册/找回密码/访客/第三方 ID 入口全部隐藏，用户点击登录后进入 SSO
cli/tests/test_matrix_chart.py 断言渲染结果里所有 URL 的主机只有 {chat,matrix,id}.<domain>。
*/ -}}
{{- define "matrix.elementConfig" -}}
{{- $d := required "domain (SITE_DOMAIN) is required" .Values.domain -}}
{{- $off := dict -}}
{{- range list "urlPreviews" "feedback" "voip" "widgets" "registration" "passwordReset" "deactivate" "identityServer" "thirdPartyId" "locationSharing" "shareSocial" "advancedEncryption" -}}
{{- $_ := set $off (printf "UIFeature.%s" .) false -}}
{{- end -}}
{{- $_ := set $off "language" .Values.element.defaultLanguage -}}
{{- $cfg := dict
  "default_server_config" (dict "m.homeserver" (dict "base_url" (printf "https://matrix.%s" (default .Values.domain .Values.publicDomain)) "server_name" $d))
  "disable_custom_urls" true
  "disable_guests" true
  "disable_3pid_login" true
  "brand" "chentu"
  "default_country_code" "CN"
  "default_theme" "light"
  "default_federate" false
  "show_labs_settings" false
  "features" (dict)
  "room_directory" (dict "servers" (list $d))
  "integrations_ui_url" nil
  "integrations_rest_url" nil
  "integrations_widgets_urls" nil
  "element_call" (dict "disable" true "use_exclusively" false)
  "desktop_builds" (dict "available" false "logo" "" "url" "")
  "mobile_builds" (dict "ios" nil "android" nil "fdroid" nil)
  "mobile_guide_toast" false
  "help_url" (printf "https://chat.%s/" (default .Values.domain .Values.publicDomain))
  "sso_redirect_options" (dict "immediate" false)
  "setting_defaults" $off
-}}
{{- toPrettyJson $cfg }}
{{- end -}}

{{- /*
Element 从 server_name 的根域取 client well-known（matrix-js-sdk AutoDiscovery）。Tuwunel 自己的
/.well-known/matrix/client 只能写 base_url，写不了 io.element.e2ee，所以由 Element 的 nginx 在根域上发：
force_disable 让 Element 不再提供"开启加密"的开关（服务端 allow_encryption=false 才是强制的那一层）。
*/ -}}
{{- define "matrix.wellKnownClient" -}}
{{- $d := required "domain (SITE_DOMAIN) is required" .Values.domain -}}
{{- toPrettyJson (dict
  "m.homeserver" (dict "base_url" (printf "https://matrix.%s" (default .Values.domain .Values.publicDomain)))
  "io.element.e2ee" (dict "default" false "force_disable" true)) }}
{{- end -}}
