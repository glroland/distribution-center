{{/*
Base name for the release.
*/}}
{{- define "adc.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else -}}
{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end -}}
{{- end -}}

{{/*
Labels shared by every resource in the chart.
*/}}
{{- define "adc.commonLabels" -}}
app.kubernetes.io/part-of: ai-distribution-center
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/*
Full image reference: <fullNameOverride style="repository[:tag]">, prefixed
with global.imageRegistry when set.
Usage: {{ include "adc.image" (dict "root" $ "image" .someComponent.image) }}
*/}}
{{- define "adc.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- if $registry -}}
{{ printf "%s/%s:%s" (trimSuffix "/" $registry) .image.repository .image.tag }}
{{- else -}}
{{ printf "%s:%s" .image.repository .image.tag }}
{{- end -}}
{{- end -}}

{{/*
Name of the global po-ingest-api Service.
*/}}
{{- define "adc.poIngestApi.serviceName" -}}
{{ include "adc.fullname" . }}-po-ingest-api
{{- end -}}

{{/*
Name of the global supervisor-api Service.
*/}}
{{- define "adc.supervisorApi.serviceName" -}}
{{ include "adc.fullname" . }}-supervisor-api
{{- end -}}

{{/*
Name of the OpenAI secret consumed by dc-agent deployments.
*/}}
{{- define "adc.openai.secretName" -}}
{{- if .Values.openai.existingSecret -}}
{{ .Values.openai.existingSecret }}
{{- else -}}
{{ include "adc.fullname" . }}-openai
{{- end -}}
{{- end -}}

{{/*
Per-distribution-center Service name for a given component suffix.
Usage: {{ include "adc.dc.serviceName" (dict "root" $ "dc" . "component" "wms-api") }}
*/}}
{{- define "adc.dc.serviceName" -}}
{{ include "adc.fullname" .root }}-{{ .dc.name }}-{{ .component }}
{{- end -}}

{{/*
Pod-level securityContext. Left mostly empty so OpenShift's restricted-v2
SCC assigns a UID/fsGroup from the project's allocated range; override via
global.podSecurityContext if your cluster requires explicit values.
*/}}
{{- define "adc.podSecurityContext" -}}
{{- toYaml .Values.global.podSecurityContext -}}
{{- end -}}

{{/*
Container-level securityContext: hardened defaults that are compatible
with OpenShift's restricted-v2 SCC (no fixed runAsUser/fsGroup).
*/}}
{{- define "adc.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}
