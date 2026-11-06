{{- define "matrix.image" -}}
{{- $i := index .context.Values.images .role -}}
{{- if .context.Values.global.imageRegistry }}{{ .context.Values.global.imageRegistry }}/{{ end }}{{ $i.repository }}{{ if $i.digest }}@{{ $i.digest }}{{ else }}:{{ $i.tag }}{{ end }}
{{- end -}}
{{- define "chat.origin" -}}{{ default (printf "https://chat.%s" .Values.domain) .Values.publicUrl }}{{- end -}}
{{- define "chat.homeserver" -}}{{ default (printf "https://matrix.%s" .Values.domain) .Values.homeserverUrl }}{{- end -}}
{{- define "chat.clientId" -}}{{ default (printf "vasi-%s-%s" .Release.Namespace .Release.Name) .Values.auth.oidc.clientId }}{{- end -}}
{{- define "chat.issuer" -}}{{ default (printf "https://iam.%s/realms/master" .Values.domain) .Values.auth.oidc.issuer }}{{- end -}}
