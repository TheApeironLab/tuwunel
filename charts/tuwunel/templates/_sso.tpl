{{- define "chat.origin" -}}{{ default (printf "https://chat.%s" (default .Values.domain .Values.publicDomain)) .Values.publicUrl }}{{- end -}}
{{- define "chat.homeserver" -}}{{ default (printf "https://matrix.%s" (default .Values.domain .Values.publicDomain)) .Values.homeserverUrl }}{{- end -}}
{{- define "chat.issuer" -}}{{ default (printf "https://iam.%s/realms/master" (default .Values.domain .Values.publicDomain)) .Values.auth.oidc.issuer }}{{- end -}}
{{- define "chat.clientId" -}}{{ default "matrix-app" .Values.auth.oidc.clientId }}{{- end -}}
