"""Renders a flat EvalHub provider/collection spec (config/evalhub-provider.yaml,
config/evalhub-collection.yaml) into the labeled ConfigMap manifest EvalHub's
operator actually reconciles the EvalHub CR's spec.providers/spec.collections
against (see evalhub-provider.yaml's header comment for why this ConfigMap
mechanism -- not `evalhub providers create --file` -- is the one that shows up
in the OpenShift AI dashboard and gets used by `evalhub eval run`/`make
evalhub-run`).

`evalhub providers create` is a plain create, not an upsert: re-running it
against an existing name-registered-elsewhere provider silently created a
second, disconnected provider with a stale one still wired to the dashboard
(see git history / `make register-eval-suite`'s old implementation) -- a
rebuilt image's new tag never reached the ConfigMap that actually matters.
`oc apply` on this rendered ConfigMap is a real upsert instead: same
resource, updated in place, so `make register-eval-suite` picks up
config/evalhub-provider.yaml's current runtime.k8s.image every time it's run,
not just the first time.
"""

import argparse
import sys

import yaml

NAMESPACE = "redhat-ods-applications"
COMMON_LABELS = {
    "app.kubernetes.io/part-of": "trustyai",
    "app.opendatahub.io/trustyai": "true",
    "platform.opendatahub.io/part-of": "trustyai",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["provider", "collection"])
    parser.add_argument("spec_file")
    parser.add_argument("resource_id")
    parser.add_argument("configmap_name")
    args = parser.parse_args()

    with open(args.spec_file) as f:
        spec = yaml.safe_load(f)
    spec["id"] = args.resource_id

    labels = {
        **COMMON_LABELS,
        f"trustyai.opendatahub.io/evalhub-{args.kind}-name": args.resource_id,
        # Must be literally "system", not e.g. "custom" -- confirmed by
        # testing "custom" first, which the operator's ConfigMap lookup
        # never matched (see evalhub-provider.yaml's header comment).
        f"trustyai.opendatahub.io/evalhub-{args.kind}-type": "system",
    }

    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": args.configmap_name,
            "namespace": NAMESPACE,
            "labels": labels,
        },
        "data": {f"{args.resource_id}.yaml": yaml.dump(spec, sort_keys=False)},
    }
    yaml.dump(configmap, sys.stdout, sort_keys=False)


if __name__ == "__main__":
    main()
