{{- define "spatial-masks.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "spatial-masks.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "spatial-masks.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "spatial-masks.labels" -}}
app.kubernetes.io/name: {{ include "spatial-masks.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "spatial-masks.dbHost" -}}
{{- if .Values.postgres.deploy -}}
{{ include "spatial-masks.fullname" . }}-db
{{- else -}}
{{ .Values.postgres.host }}
{{- end -}}
{{- end -}}

{{- define "spatial-masks.dbSecretName" -}}
{{- if .Values.postgres.existingSecret -}}
{{ .Values.postgres.existingSecret }}
{{- else -}}
{{ include "spatial-masks.fullname" . }}-db
{{- end -}}
{{- end -}}

{{- define "spatial-masks.dbPasswordKey" -}}
{{- if .Values.postgres.existingSecret -}}
{{ .Values.postgres.existingSecretPasswordKey }}
{{- else -}}
POSTGRES_PASSWORD
{{- end -}}
{{- end -}}

{{/*
Блок env с паролем из секрета и собранным в рантайме DATABASE_URL.
Использование: {{ include "spatial-masks.dbEnv" (dict "ctx" . "scheme" "postgresql+psycopg") }}
*/}}
{{- define "spatial-masks.dbEnv" -}}
{{- $ := .ctx -}}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "spatial-masks.dbSecretName" $ }}
      key: {{ include "spatial-masks.dbPasswordKey" $ }}
- name: DATABASE_URL
  value: "{{ .scheme }}://{{ $.Values.postgres.user }}:$(DB_PASSWORD)@{{ include "spatial-masks.dbHost" $ }}:{{ $.Values.postgres.port }}/{{ $.Values.postgres.database }}"
{{- end -}}

{{- define "spatial-masks.publicBase" -}}
{{- if .Values.public.url -}}
{{- .Values.public.url -}}
{{- else -}}
{{- printf "%s://%s" .Values.public.scheme .Values.ingress.host -}}
{{- end -}}
{{- end -}}
