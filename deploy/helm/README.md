# ai-distribution-center Helm chart

Deploys the whole application to OpenShift as an umbrella chart with one
subchart per component group, under `charts/`:

- **`charts/poIngestApi`** -- the global PO submission entry point (singleton).
- **`charts/supervisorApi`** -- the global human-escalation service (singleton).
- **`charts/distributionCenter`** -- one or more self-contained "local"
  groupings, each rendering `local-dc-agent`, `local-wms-api`,
  `local-inventory-robot-api`, `local-shipping-api` with 1 replica each. The
  `local-*` components in a distribution center are wired to talk only to
  each other (plus the two global services) via in-cluster Service DNS;
  they never reach across into another distribution center's grouping.
  Driven entirely by the `distributionCenter.centers` list in the umbrella
  chart's `values.yaml` -- ships with one entry, "Distribution Center A".
  Add more entries to that list to stand up additional, fully isolated
  distribution centers; no template changes or new files required.

Values under `global:` (image registry/pull settings, security context,
OpenShift route settings, OpenAI settings) are shared automatically across
every subchart. Everything else in the umbrella `values.yaml` is scoped to
one subchart by name (`poIngestApi:`, `supervisorApi:`,
`distributionCenter:`) and overrides that subchart's own defaults.

Per-service inventory/shelf data is seeded from ConfigMaps
(`distributionCenter.centers[].wmsApi.inventoryCsv`,
`...robotApi.shelvesCsv` in `values.yaml`), mounted over each container's
`data/*.csv` path -- override these per distribution center to give each
one its own starting inventory.

## Build and push images

The app repos build with Podman/Buildah `Containerfile`s, not Dockerfiles.
Tag and push each one to a registry the cluster can pull from (e.g.
OpenShift's internal registry):

```sh
for c in po-ingest-api supervisor-api local-dc-agent local-wms-api \
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

## Adding a distribution center

Copy the `distributionCenter.centers[0]` block in `values.yaml`, give it a
new `name` (used to derive Service/ConfigMap names -- keep it a short,
DNS-safe slug) and `displayName`, and adjust `locationName` /
`inventoryCsv` / `shelvesCsv` as needed. Each distribution center gets its
own fully-namespaced set of Deployments, Services, and ConfigMaps -- no
manual wiring, and no new subchart or dependency entry required.

## Notes for OpenShift

- Containers run with a hardened `securityContext` (non-root, all
  capabilities dropped, no privilege escalation) and intentionally leave
  `runAsUser`/`fsGroup` unset so the project's `restricted-v2` SCC assigns
  them -- no `anyuid` SCC binding required.
- `route.openshift.io/v1` Routes are created for `po-ingest-api` (PO
  submission entry point) and each distribution center's `dc-agent` (A2A
  endpoint) by default; toggle via `global.openshift.routes.enabled` and
  each component's `route.enabled`. Set `global.openshift.routes.enabled=false`
  to deploy to plain Kubernetes instead.

## Useful commands

```sh
helm lint deploy/helm
helm template adc deploy/helm --set global.openai.apiKey=sk-test
helm install adc deploy/helm -f my-values.yaml
helm upgrade adc deploy/helm -f my-values.yaml
```
