# ai-distribution-center Helm chart

Deploys the whole application to OpenShift as an umbrella chart with one
subchart per component group, under `charts/`:

- **`charts/poIngestApi`** -- the global PO submission entry point (singleton).
- **`charts/supervisorApi`** -- the global human-escalation service (singleton).
- **`charts/dashboardUi`** -- the demo control room UI (singleton). Its
  `DISTRIBUTION_CENTER_JSON` env var is rendered from
  `dashboardUi.distributionCenter` in `values.yaml`, which hand-mirrors
  `distributionCenter` (name + ports) so it can address the DC's in-cluster
  Service DNS -- see `charts/dashboardUi/values.yaml` for why this can't
  just reference `distributionCenter` directly.
- **`charts/distributionCenter`** -- the single self-contained "local"
  grouping, rendering `local-dc-agent`, `local-wms-api`,
  `local-inventory-robot-api`, `local-shipping-api` with 1 replica each. The
  `local-*` components are wired to talk only to each other (plus the two
  global services) via in-cluster Service DNS. Driven by the
  `distributionCenter` block in the umbrella chart's `values.yaml`.

Values under `global:` (image registry/pull settings, security context,
OpenShift route settings, OpenAI settings) are shared automatically across
every subchart. Everything else in the umbrella `values.yaml` is scoped to
one subchart by name (`poIngestApi:`, `supervisorApi:`,
`distributionCenter:`) and overrides that subchart's own defaults.

Per-service inventory/shelf data is seeded from ConfigMaps
(`distributionCenter.wmsApi.inventoryCsv`, `...robotApi.shelvesCsv` in
`values.yaml`), mounted over each container's `data/*.csv` path.

## Build and push images

The app repos build with Podman/Buildah `Containerfile`s, not Dockerfiles.
Tag and push each one to a registry the cluster can pull from (e.g.
OpenShift's internal registry):

```sh
for c in po-ingest-api supervisor-api dashboard-ui local-dc-agent local-wms-api \
         local-inventory-robot-api local-shipping-api; do
  podman build -t "$REGISTRY/$NAMESPACE/$c:latest" -f "$c/Containerfile" "$c"
  podman push "$REGISTRY/$NAMESPACE/$c:latest"
done
```

Then point the chart at that registry:

```sh
helm install adc deploy/helm \
  --set global.imageRegistry="$REGISTRY/$NAMESPACE" \
  --set global.openai.apiKey="$OPENAI_API_KEY"
```

If pulling from OpenShift's internal registry from within the same
project, `imagePullSecrets` usually isn't needed; for anything else, set
`global.imagePullSecrets`.

## OpenAI credentials

The `dc-agent` in every distribution center needs `OPENAI_API_KEY`. Either
pass it directly (`--set global.openai.apiKey=...`, dev/test only) or point
at a secret you create yourself:

```sh
oc create secret generic my-openai-secret --from-literal=OPENAI_API_KEY=sk-...
helm install adc deploy/helm --set global.openai.existingSecret=my-openai-secret
```

## MLflow tracing

Every service that makes LLM/MCP calls (`poIngestApi`, `supervisorApi`, and
every `local-*` component in each distribution center) gets
`MLFLOW_TRACKING_URI`, `MLFLOW_WORKSPACE`, `MLFLOW_EXPERIMENT_NAME`, and
`MLFLOW_TRACKING_AUTH` from a single shared ConfigMap
(`templates/mlflow-configmap.yaml`), controlled by `global.mlflow.*` in
`values.yaml`. `dashboardUi` is deliberately excluded -- it makes no
LLM/MCP calls of its own, just REST/SSE passthrough.

```sh
helm install adc deploy/helm \
  --set global.openai.apiKey="$OPENAI_API_KEY" \
  --set global.mlflow.trackingUri=https://your-mlflow-server/
```

`global.mlflow.trackingAuth` defaults to `kubernetes-namespaced`, which
relies on `mlflow[kubernetes]` (bundled in every service's
`requirements.txt`) reading the pod's own service-account token and
namespace automatically -- there's no token Secret to create, rotate, or
pass in. Set it to `""` if your MLflow server doesn't authenticate that way.

To also dual-export traces to a separate OTLP collector (e.g. Tempo/Jaeger)
alongside MLflow, set `global.mlflow.otlpEndpoint` (and `otlpHeaders` if
needed); leave both empty to skip OTLP entirely. Set
`global.mlflow.enabled=false` to omit all of this and run without tracing.

`kubernetes-namespaced` auth only works for a service account RHOAI has
explicitly granted `mlflow.kubeflow.org` access to -- being in-namespace
isn't enough. Every Deployment in this chart runs as the namespace's
`default` SA, which RHOAI grants automatically, but `vision-ml`'s KFP
pipeline (`../../vision-ml/src/pipeline.py`) runs as a separate
`pipeline-runner-<dspa-name>` SA that RHOAI does *not* grant by default --
without it, pipeline runs fail with `PERMISSION_DENIED` calling
`mlflow.set_experiment`. `templates/mlflow-pipeline-runner-rolebinding.yaml`
binds each name in `global.mlflow.pipelineRunnerServiceAccounts` (default:
`pipeline-runner-dspa`) to `global.mlflow.integrationClusterRole` -- the same
ClusterRole RHOAI auto-binds to its own workbench pods. Add your DSPA's
pipeline-runner SA name to that list if it differs from the default.

## Notes for OpenShift

- Containers run with a hardened `securityContext` (non-root, all
  capabilities dropped, no privilege escalation) and intentionally leave
  `runAsUser`/`fsGroup` unset so the project's `restricted-v2` SCC assigns
  them -- no `anyuid` SCC binding required.
- `route.openshift.io/v1` Routes are created for `po-ingest-api` (PO
  submission entry point), `dashboard-ui` (the demo UI), and the
  distribution center's `dc-agent` (A2A endpoint) by default; toggle via
  `global.openshift.routes.enabled` and each component's `route.enabled`.
  Set `global.openshift.routes.enabled=false` to deploy to plain Kubernetes
  instead.

## Useful commands

```sh
helm lint deploy/helm
helm template adc deploy/helm --set global.openai.apiKey=sk-test
helm install adc deploy/helm -f my-values.yaml
helm upgrade adc deploy/helm -f my-values.yaml
```
