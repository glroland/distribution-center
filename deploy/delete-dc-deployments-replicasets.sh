#!/bin/bash
#
# Deletes all Deployments and ReplicaSets belonging to the AI Distribution
# Center app (dc-agent, wms-api, robot-api, shipping-api, po-ingest-api,
# supervisor-api, dashboard-ui, label-api) from an OpenShift namespace.
#
# Scoped with the Helm chart's app.kubernetes.io/part-of=ai-distribution-center
# label (see deploy/helm/templates/_helpers.tpl) rather than deleting every
# Deployment/ReplicaSet in the namespace, so unrelated workloads are untouched.
#
# Usage:
#   deploy/delete-dc-deployments-replicasets.sh [-n NAMESPACE] [-y] [--dry-run]
#
#   -n NAMESPACE   Namespace to target (default: distribution-center)
#   -y             Skip the confirmation prompt
#   --dry-run      Show what would be deleted without deleting anything

set -euo pipefail

NAMESPACE="distribution-center"
LABEL_SELECTOR="app.kubernetes.io/part-of=ai-distribution-center"
ASSUME_YES=0
DRY_RUN=0

# Component names for reference/logging only -- the actual selection is
# done via LABEL_SELECTOR above so this stays in sync automatically even
# if a component is renamed.
COMPONENTS=(
	dc-agent
	wms-api
	robot-api
	shipping-api
	po-ingest-api
	supervisor-api
	dashboard-ui
	label-api
)

while [[ $# -gt 0 ]]; do
	case "$1" in
	-n)
		NAMESPACE="$2"
		shift 2
		;;
	-y)
		ASSUME_YES=1
		shift
		;;
	--dry-run)
		DRY_RUN=1
		shift
		;;
	*)
		echo "Unknown argument: $1" >&2
		exit 1
		;;
	esac
done

if ! command -v oc >/dev/null 2>&1; then
	echo "error: 'oc' CLI not found on PATH" >&2
	exit 1
fi

echo "Namespace:      $NAMESPACE"
echo "Label selector: $LABEL_SELECTOR"
echo "Components:     ${COMPONENTS[*]}"
echo

echo "Deployments matched:"
oc get deployments -n "$NAMESPACE" -l "$LABEL_SELECTOR" --no-headers 2>/dev/null || true
echo
echo "ReplicaSets matched:"
oc get replicasets -n "$NAMESPACE" -l "$LABEL_SELECTOR" --no-headers 2>/dev/null || true
echo

if [[ "$DRY_RUN" -eq 1 ]]; then
	echo "Dry run -- nothing deleted."
	exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
	read -r -p "Delete the above Deployments and ReplicaSets in '$NAMESPACE'? [y/N] " reply
	if [[ ! "$reply" =~ ^[Yy]$ ]]; then
		echo "Aborted."
		exit 0
	fi
fi

oc delete deployments -n "$NAMESPACE" -l "$LABEL_SELECTOR"
oc delete replicasets -n "$NAMESPACE" -l "$LABEL_SELECTOR"

echo "Done."
