{{- define "co.fullname" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "co.labels" -}}
app.kubernetes.io/name: conferenceops
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default "unset" | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "co.selector" -}}
app.kubernetes.io/name: conferenceops
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* An image reference from (list "<values path>" <its {repository, tag} values>). Both are required, and the tag is
     never latest (K8S-1); the error names the value to set. */}}
{{- define "co.imageRef" -}}
{{- $path := index . 0 -}}
{{- $image := index . 1 -}}
{{- if not $image.repository -}}{{- fail (printf "%s.repository is required" $path) -}}{{- end -}}
{{- if not $image.tag -}}{{- fail (printf "%s.tag is required (the commit SHA, never latest)" $path) -}}{{- end -}}
{{- if eq (toString $image.tag) "latest" -}}{{- fail (printf "%s.tag must not be latest (K8S-1)" $path) -}}{{- end -}}
{{ $image.repository }}:{{ $image.tag }}
{{- end -}}

{{/* The backend image: the APIs, the worker and the migrations Job. */}}
{{- define "co.image" -}}
{{- include "co.imageRef" (list "image" .Values.image) -}}
{{- end -}}

{{/* The web image, built from apps/web. */}}
{{- define "co.webImage" -}}
{{- include "co.imageRef" (list "web.image" .Values.web.image) -}}
{{- end -}}

{{/* Container security settings shared by every workload we own (K8S-1). */}}
{{- define "co.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}

{{/* Environment shared by the APIs, the worker and the migrations Job. */}}
{{- define "co.env" -}}
- name: ENVIRONMENT
  value: {{ .Values.environment | quote }}
- name: SERVICE_VERSION
  value: {{ .Values.image.tag | quote }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: http://conferenceops-collector:4318
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ .Values.dbSecretName }}, key: password}
- name: DATABASE_URL
  value: postgresql+psycopg://conferenceops:$(DB_PASSWORD)@{{ include "co.fullname" . }}-postgres:5432/conferenceops
- name: ELASTICSEARCH_URL
  value: http://{{ include "co.fullname" . }}-es-http:9200
- name: ELASTICSEARCH_USERNAME
  value: elastic
- name: ELASTICSEARCH_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "co.fullname" . }}-es-elastic-user, key: elastic}
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef: {name: {{ .Values.secretName }}, key: OPENAI_API_KEY, optional: true}
{{- end -}}
