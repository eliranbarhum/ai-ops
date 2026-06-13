{{/*
Common labels applied to all resources.
*/}}
{{- define "mco.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: mco
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/*
Image reference for a given service name.
Usage: {{ include "mco.image" (dict "name" "api-gateway" "root" .) }}
*/}}
{{- define "mco.image" -}}
{{ .root.Values.image.registry }}/{{ .name }}:{{ .root.Values.image.tag }}
{{- end }}

{{/*
Image pull policy + optional pull secret.
*/}}
{{- define "mco.imagePullPolicy" -}}
imagePullPolicy: {{ .Values.image.pullPolicy }}
{{- end }}

{{- define "mco.imagePullSecrets" -}}
{{- if .Values.image.pullSecret }}
imagePullSecrets:
  - name: {{ .Values.image.pullSecret }}
{{- end }}
{{- end }}

{{/*
Linkerd injection annotation — only when linkerd.enabled=true.
*/}}
{{- define "mco.linkerdAnnotation" -}}
{{- if .Values.linkerd.enabled }}
linkerd.io/inject: enabled
{{- end }}
{{- end }}

{{/*
Standard resource block. Pass either .Values.resources.default or a specific block.
*/}}
{{- define "mco.resources" -}}
resources:
  requests:
    cpu: {{ .requests.cpu }}
    memory: {{ .requests.memory }}
  limits:
    cpu: {{ .limits.cpu }}
    memory: {{ .limits.memory }}
{{- end }}

{{/*
Namespace shorthand.
*/}}
{{- define "mco.namespace" -}}
{{ .Values.namespace }}
{{- end }}
