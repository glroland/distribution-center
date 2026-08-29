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
# Usage:
#   deploy/apply-openai-secret.sh <OPENAI_API_KEY> [-n NAMESPACE] [--name SECRET_NAME]
#
#   <OPENAI_API_KEY>    Required. The key to store.
#   -n NAMESPACE        Namespace to apply into (default: distribution-center)
#   --name SECRET_NAME  Secret name (default: distribution-center-credentials,
#                       matching global.openai.existingSecret's default)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/openai-secret.template.yaml"

NAMESPACE="distribution-center"
SECRET_NAME="distribution-center-credentials"

if [[ $# -eq 0 ]]; then
	echo "usage: $(basename "$0") <OPENAI_API_KEY> [-n NAMESPACE] [--name SECRET_NAME]" >&2
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

export SECRET_NAME OPENAI_API_KEY

echo "Namespace:   $NAMESPACE"
echo "Secret name: $SECRET_NAME"

envsubst '${SECRET_NAME} ${OPENAI_API_KEY}' <"$TEMPLATE" | oc apply -n "$NAMESPACE" -f -

echo "Done."
