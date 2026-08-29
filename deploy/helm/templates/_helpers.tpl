{{/*
Base name used to prefix every resource this chart creates. Deliberately
NOT derived from .Release.Name -- the Argo Application/Helm release is
named "distribution-center" for readability, but OpenShift Route hostnames
(<route-name>-<namespace>.<router-suffix>) blow past the 63-character DNS
label limit if every resource name starts with that. Defaults to the short
"adc" instead; override via fullnameOverride if you need something else.
*/}}
{{- define "adc.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else -}}
adc
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
with global.imageRegistry when set. The tag always comes from
global.imageTag -- every image in this chart is built and tagged together,
so components never carry their own independent tag.
Usage: {{ include "adc.image" (dict "root" $ "image" .someComponent.image) }}
*/}}
{{- define "adc.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- $tag := .root.Values.global.imageTag -}}
{{- if $registry -}}
{{ printf "%s/%s:%v" (trimSuffix "/" $registry) .image.repository $tag }}
{{- else -}}
{{ printf "%s:%v" .image.repository $tag }}
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
Name of the global label-api Service.
*/}}
{{- define "adc.labelApi.serviceName" -}}
{{ include "adc.fullname" . }}-label-api
{{- end -}}

{{/*
Name of the OpenAI secret consumed by dc-agent deployments. Never rendered
by this chart -- global.openai.existingSecret must name a Secret you
created yourself (see deploy/apply-openai-secret.sh).
*/}}
{{- define "adc.openai.secretName" -}}
{{ required "global.openai.existingSecret is required -- create it first with deploy/apply-openai-secret.sh" .Values.global.openai.existingSecret }}
{{- end -}}

{{/*
Name of the dc-agent Service.
*/}}
{{- define "adc.dcAgent.serviceName" -}}
{{ include "adc.fullname" . }}-dc-agent
{{- end -}}

{{/*
Name of the wms-api Service.
*/}}
{{- define "adc.wmsApi.serviceName" -}}
{{ include "adc.fullname" . }}-wms-api
{{- end -}}

{{/*
Name of the robot-api Service.
*/}}
{{- define "adc.robotApi.serviceName" -}}
{{ include "adc.fullname" . }}-robot-api
{{- end -}}

{{/*
Name of the shipping-api Service.
*/}}
{{- define "adc.shippingApi.serviceName" -}}
{{ include "adc.fullname" . }}-shipping-api
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

{{/*
Name of the shared MLflow tracing ConfigMap (deploy/helm/templates/mlflow-configmap.yaml).
*/}}
{{- define "adc.mlflow.configMapName" -}}
{{ include "adc.fullname" . }}-mlflow
{{- end -}}

{{/*
Standard MLflow tracing env vars for a container, sourced from the shared
ConfigMap. Include inside any container's `env:` list:
`{{- include "adc.mlflow.envVars" $root | nindent 12 }}`.
Renders nothing when global.mlflow.enabled is false.
*/}}
{{- define "adc.mlflow.envVars" -}}
{{- if .Values.global.mlflow.enabled }}
- name: PROMPT_SOURCE
  value: "mlflow"
- name: MLFLOW_TRACKING_URI
  valueFrom:
    configMapKeyRef:
      name: {{ include "adc.mlflow.configMapName" . }}
      key: MLFLOW_TRACKING_URI
- name: MLFLOW_WORKSPACE
  valueFrom:
    configMapKeyRef:
      name: {{ include "adc.mlflow.configMapName" . }}
      key: MLFLOW_WORKSPACE
- name: MLFLOW_EXPERIMENT_NAME
  valueFrom:
    configMapKeyRef:
      name: {{ include "adc.mlflow.configMapName" . }}
      key: MLFLOW_EXPERIMENT_NAME
- name: MLFLOW_TRACKING_AUTH
  valueFrom:
    configMapKeyRef:
      name: {{ include "adc.mlflow.configMapName" . }}
      key: MLFLOW_TRACKING_AUTH
{{- if .Values.global.mlflow.otlpEndpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  valueFrom:
    configMapKeyRef:
      name: {{ include "adc.mlflow.configMapName" . }}
      key: OTEL_EXPORTER_OTLP_ENDPOINT
{{- end }}
{{- if .Values.global.mlflow.otlpHeaders }}
- name: OTEL_EXPORTER_OTLP_HEADERS
  valueFrom:
    configMapKeyRef:
      name: {{ include "adc.mlflow.configMapName" . }}
      key: OTEL_EXPORTER_OTLP_HEADERS
{{- end }}
{{- end }}
{{- end -}}
