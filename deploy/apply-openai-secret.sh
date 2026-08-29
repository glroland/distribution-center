#!/bin/bash
#
# Applies (creates or updates) the OpenAI credentials Secret that dc-agent
# reads via global.openai.existingSecret / global.openai.existingSecretKey
# (deploy/helm/values.yaml), from deploy/openai-secret.template.yaml.
#
# Deliberately NOT managed by the Helm chart -- this keeps the API key out
# of `helm install/upgrade --set` and out of values.yaml entirely; run this
# script once (or whenever the key rotates) against each namespace instead.
#
# Kubernetes never restarts a running pod just because a Secret it
# references via env var (secretKeyRef) changed -- the value is resolved
# once, when the container starts. So after applying, this script also
# rolls (oc rollout restart) every Deployment in the namespace whose pod
# spec actually references this Secret, found dynamically via jq rather
# than hardcoded to dc-agent's Deployment name (which is prefixed by the
# Helm release name and so isn't fixed). Pass --no-restart to skip this and
# roll the affected Deployment(s) yourself later.
#
# Usage:
#   deploy/apply-openai-secret.sh <OPENAI_API_KEY> [-n NAMESPACE] [--name SECRET_NAME] [--no-restart]
#
#   <OPENAI_API_KEY>    Required. The key to store.
#   -n NAMESPACE        Namespace to apply into (default: distribution-center)
#   --name SECRET_NAME  Secret name (default: distribution-center-credentials,
#                       matching global.openai.existingSecret's default)
#   --no-restart        Apply the Secret only; don't roll any Deployments

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/openai-secret.template.yaml"

NAMESPACE="distribution-center"
SECRET_NAME="distribution-center-credentials"
DO_RESTART=1

if [[ $# -eq 0 ]]; then
	echo "usage: $(basename "$0") <OPENAI_API_KEY> [-n NAMESPACE] [--name SECRET_NAME] [--no-restart]" >&2
	exit 1
fi

OPENAI_API_KEY="$1"
shift

while [[ $# -gt 0 ]]; do
	case "$1" in
	-n)
		NAMESPACE="$2"
		shift 2
		;;
	--name)
		SECRET_NAME="$2"
		shift 2
		;;
	--no-restart)
		DO_RESTART=0
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

if ! command -v envsubst >/dev/null 2>&1; then
	echo "error: 'envsubst' not found on PATH (part of gettext)" >&2
	exit 1
fi

if [[ "$DO_RESTART" -eq 1 ]] && ! command -v jq >/dev/null 2>&1; then
	echo "error: 'jq' not found on PATH (needed to find Deployments to restart; pass --no-restart to skip)" >&2
	exit 1
fi

export SECRET_NAME OPENAI_API_KEY

echo "Namespace:   $NAMESPACE"
echo "Secret name: $SECRET_NAME"

# --server-side avoids `oc apply`'s usual kubectl.kubernetes.io/last-applied-configuration
# annotation, which would otherwise store this Secret's stringData -- API key
# included -- in plaintext on the object itself (readable via `oc get -o yaml`
# even though the top-level `data` field is base64). --force-conflicts lets
# this script keep re-claiming the fields it manages across runs.
envsubst '${SECRET_NAME} ${OPENAI_API_KEY}' <"$TEMPLATE" | oc apply -n "$NAMESPACE" --server-side --force-conflicts -f -

if [[ "$DO_RESTART" -eq 0 ]]; then
	echo "Done (--no-restart: restart any Deployment using this Secret yourself for the new key to take effect)."
	exit 0
fi

mapfile -t DEPLOYMENTS < <(
	oc get deployments -n "$NAMESPACE" -o json |
		jq -r --arg secret "$SECRET_NAME" '
			.items[]
			| select(
				[.spec.template.spec.containers[]?, .spec.template.spec.initContainers[]?]
				| any(.env[]?.valueFrom.secretKeyRef.name == $secret)
			)
			| .metadata.name
		'
)

if [[ ${#DEPLOYMENTS[@]} -eq 0 ]]; then
	echo "No Deployment in '$NAMESPACE' references Secret '$SECRET_NAME' -- nothing to restart."
	exit 0
fi

for d in "${DEPLOYMENTS[@]}"; do
	echo "Restarting deployment/$d to pick up the new key..."
	oc rollout restart "deployment/$d" -n "$NAMESPACE"
done

echo "Done."
