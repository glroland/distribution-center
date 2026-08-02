{{/*
Name of this dashboard's own Service.
*/}}
{{- define "adc.dashboardUi.serviceName" -}}
{{ include "adc.fullname" . }}-dashboard-ui
{{- end -}}

{{/*
Renders .Values.distributionCenters as the JSON array dashboard-ui/src/settings.py's
DISTRIBUTION_CENTERS_JSON expects: one object per DC with the field names
DistributionCenter (src/models.py) requires, urls pointing at each DC's
in-cluster Service DNS names via the shared adc.dc.serviceName helper.
*/}}
{{- define "adc.dashboardUi.distributionCentersJson" -}}
{{- $root := . -}}
{{- $entries := list -}}
{{- range $dc := .Values.distributionCenters -}}
{{- $agentSvc := include "adc.dc.serviceName" (dict "root" $root "dc" $dc "component" "dc-agent") -}}
{{- $wmsSvc := include "adc.dc.serviceName" (dict "root" $root "dc" $dc "component" "wms-api") -}}
{{- $robotSvc := include "adc.dc.serviceName" (dict "root" $root "dc" $dc "component" "robot-api") -}}
{{- $shippingSvc := include "adc.dc.serviceName" (dict "root" $root "dc" $dc "component" "shipping-api") -}}
{{- $entries = append $entries (dict
    "name" $dc.name
    "display_name" $dc.displayName
    "agent_url" (printf "http://%s:%v" $agentSvc $dc.dcAgentPort)
    "wms_url" (printf "http://%s:%v" $wmsSvc $dc.wmsApiPort)
    "robot_url" (printf "http://%s:%v" $robotSvc $dc.robotApiPort)
    "shipping_url" (printf "http://%s:%v" $shippingSvc $dc.shippingApiPort)
    "grid_width" $dc.grid.width
    "grid_height" $dc.grid.height
    "dock_x" $dc.dock.x
    "dock_y" (index $dc.dock "y")
  ) -}}
{{- end -}}
{{- toJson $entries -}}
{{- end -}}
