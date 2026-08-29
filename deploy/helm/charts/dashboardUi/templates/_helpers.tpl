{{/*
Name of this dashboard's own Service.
*/}}
{{- define "adc.dashboardUi.serviceName" -}}
{{ include "adc.fullname" . }}-dashboard-ui
{{- end -}}

{{/*
Renders .Values.distributionCenter as the JSON object dashboard-ui/src/settings.py's
DISTRIBUTION_CENTER_JSON expects: the field names DistributionCenter
(src/models.py) requires, urls pointing at the DC's in-cluster Service DNS
names via the dcAgent/wmsApi/robotApi/shippingApi serviceName helpers.
*/}}
{{- define "adc.dashboardUi.distributionCenterJson" -}}
{{- $root := . -}}
{{- $dc := .Values.distributionCenter -}}
{{- $agentSvc := include "adc.dcAgent.serviceName" $root -}}
{{- $wmsSvc := include "adc.wmsApi.serviceName" $root -}}
{{- $robotSvc := include "adc.robotApi.serviceName" $root -}}
{{- $shippingSvc := include "adc.shippingApi.serviceName" $root -}}
{{- toJson (dict
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
