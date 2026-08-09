# start-all/kill-all rely on bash-only syntax (indirect expansion, %%/# trims);
# the default /bin/sh (e.g. dash on Linux) doesn't support that.
SHELL := /bin/bash

TARGET_DIR := target
LOG_DIR := $(TARGET_DIR)/logs
PID_DIR := $(TARGET_DIR)/pids

MLFLOW_TRACKING_URI := https://rh-ai.apps.ocp.home.glroland.com/mlflow
MLFLOW_WORKSPACE := distribution-center
MLFLOW_TRACKING_TOKEN := $(shell oc whoami --show-token)

# The OpenShift AI EvalHub instance (trustyai.opendatahub.io/v1alpha1 EvalHub,
# deployed in namespace redhat-ods-applications, hence EVALHUB_BASE_URL's
# host) validates the same OpenShift bearer token as MLflow above, via k8s
# TokenReview. EVALHUB_TENANT is NOT that deployment namespace, though --
# it's sent as X-Tenant on every call and is EvalHub's notion of "which
# project is this on behalf of": for `evalhub eval run`, it's what selects
# the MLflow *workspace* an evaluation's experiment gets tracked under
# (confirmed by testing redhat-ods-applications first, which failed with
# "Workspace 'redhat-ods-applications' not found ... Each MLflow workspace
# maps 1:1 to a namespace") and determines which OpenShift AI dashboard
# project ("Distribution Center") the run shows up under -- so it must be
# MLFLOW_WORKSPACE, the same namespace dc-agent's own deployment logs to.
EVALHUB_BASE_URL := https://evalhub-redhat-ods-applications.apps.ocp.home.glroland.com
EVALHUB_TENANT := $(MLFLOW_WORKSPACE)

# name:subdir:port-env-var for every service start-all/kill-all manage.
SERVICES := \
	ingest-api:po-ingest-api:PO_INGEST_API_PORT \
	local-dc-agent:local-dc-agent:AGENT_PORT \
	local-wms-api:local-wms-api:WMS_API_PORT \
	local-inventory-robot-api:local-inventory-robot-api:ROBOT_API_PORT \
	supervisor-api:supervisor-api:SUPERVISOR_API_PORT \
	local-shipping-api:local-shipping-api:SHIPPING_API_PORT \
	label-api:label-api:LABEL_API_PORT \
	dashboard:dashboard-ui:DASHBOARD_PORT

# Every make target shares the repo-root .env (falling back to .env.example)
# instead of each submodule's own .env/.env.example. Exporting these makes
# them win over any leftover per-submodule .env when a `run-*` target cd's in.
ENV_FILE := $(if $(wildcard .env),.env,.env.example)
include $(ENV_FILE)
export

# Use uv's pip shim if uv is installed, otherwise fall back to plain pip.
ifeq ($(shell command -v uv 2>/dev/null),)
PIP := pip install
else
PIP := uv pip install
endif

# Every requirements.txt in the repo, one per dependency-bearing folder.
REQUIREMENTS := $(shell find . \
	-not -path './.git/*' \
	-not -path './$(TARGET_DIR)/*' \
	-not -path '*/.venv/*' \
	-name 'requirements*.txt')

.PHONY: help install generate-pos load-prompts eval-suite register-eval-suite evalhub-run run-ingest-api run-local-dc-agent run-local-wms-api run-local-inventory-robot-api run-supervisor-api run-local-shipping-api run-label-api run-dashboard start-all kill-all restart-all status-all clean test

help:
	@echo "Targets:"
	@echo "  install                      Install dependencies from every requirements.txt (via: $(PIP))"
	@echo "  generate-pos                 Generate sample PO PDFs into $(TARGET_DIR)/pos (ARGS=\"--count 25\" to pass flags)"
	@echo "  eval-suite                   Run the EvalHub benchmarks locally against running services (ARGS=\"--adapter extraction\" to pass flags)"
	@echo "  register-eval-suite          Register eval-suite's provider + collection with a running OpenShift AI EvalHub instance"
	@echo "  evalhub-run                  Submit a real distribution-center-eval-v1 run against the in-cluster agent (ARGS=\"--wait\" to block, ARGS=\"--watch\" to stream logs)"
	@echo "  load-prompts                 Register every <service>/prompts.json into the MLflow Prompt Registry (ARGS=\"--dry-run\" to pass flags)"
	@echo "  run-ingest-api               Run the PO ingest API (http://localhost:8000)"
	@echo "  run-local-dc-agent           Run the distribution center A2A agent (http://localhost:9100)"
	@echo "  run-local-wms-api            Run the local WMS inventory API (http://localhost:8001)"
	@echo "  run-local-inventory-robot-api Run the local inventory robot API (http://localhost:8002)"
	@echo "  run-supervisor-api           Run the local supervisor API (http://localhost:8003)"
	@echo "  run-local-shipping-api       Run the local shipping API (http://localhost:8004)"
	@echo "  run-label-api                Run the sticker label + SKU inference API (http://localhost:8005)"
	@echo "  run-dashboard                Run the demo control room UI (http://localhost:8090)"
	@echo "  start-all                    Start every service in the background (logs: $(LOG_DIR)/, pids: $(PID_DIR)/)"
	@echo "  kill-all                     Stop every service started by start-all"
	@echo "  restart-all                  Stop then start every service (kill-all + start-all)"
	@echo "  status-all                   Show which start-all services are up"
	@echo "  clean                        Remove the $(TARGET_DIR) directory"
	@echo "  test                         Unit test the application"

install:
	@echo "Using: $(PIP)"
	@for req in $(REQUIREMENTS); do \
		echo "==> $$req"; \
		$(PIP) -r "$$req" || exit 1; \
	done

generate-pos:
	@mkdir -p $(TARGET_DIR)/pos
	cd test-po-generator && python3 -m src --output-dir "../$(TARGET_DIR)/pos" $(ARGS)

load-prompts:
	cd prompt-registry && python3 -m src $(ARGS)

eval-suite:
	cd eval-suite && python3 -m src $(ARGS)

# Registers eval-suite's provider + collection (config/evalhub-provider.yaml,
# config/evalhub-collection.yaml) with a running OpenShift AI 3.4 EvalHub
# instance -- not a Kubernetes resource you apply, EvalHub exposes this as
# CLI/REST registration. Requires the `evalhub` CLI (pip install
# eval-hub-sdk) already installed. `evalhub config set` writes to
# ~/.config/evalhub/config.yaml (outside this repo), so it's safe/idempotent
# to re-run on every invocation -- re-authenticates with a fresh OpenShift
# token each time rather than relying on a previous run's (expiring) one.
# Also requires the image referenced by config/evalhub-provider.yaml's
# runtime.k8s.image already pushed (see deploy/Jenkinsfile's "Create Docker
# Image for eval-suite" stage). Two separate flat files, not one combined
# file, because the CLI's --file loads a flat mapping straight into each
# request's Pydantic model -- see evalhub-provider.yaml's header comment.
#
# Idempotent by name, not just re-runnable: EvalHub's create APIs don't
# reject a duplicate name, they'd happily mint a second provider/collection
# with a new random id every time this ran -- so this looks each up by name
# first (`evalhub providers/collections list --format json`) and reuses an
# existing one instead of re-creating. That lookup also solves
# evalhub-collection.yaml's `__PROVIDER_ID__` placeholder: the provider's
# real id is a server-assigned UUID unrelated to the `name` we send (see
# evalhub-provider.yaml's header comment), so it can only be known after
# create-or-find, then substituted into a temp copy of the collection spec.
register-eval-suite:
	evalhub config set base_url $(EVALHUB_BASE_URL)
	evalhub config set token $(MLFLOW_TRACKING_TOKEN)
	evalhub config set tenant $(EVALHUB_TENANT)
	@provider_id=$$(evalhub providers list --format json | python3 -c \
		"import json,sys; d=json.load(sys.stdin); m=[p['resource']['id'] for p in d if p['name']=='dc-eval-suite']; print(m[0] if m else '')"); \
	if [ -n "$$provider_id" ]; then \
		echo "Provider dc-eval-suite already registered: $$provider_id"; \
	else \
		out=$$(evalhub providers create --file eval-suite/config/evalhub-provider.yaml) || exit 1; \
		echo "$$out"; \
		provider_id=$$(echo "$$out" | sed -n 's/^Provider created: //p'); \
	fi; \
	collection_id=$$(evalhub collections list --format json | python3 -c \
		"import json,sys; d=json.load(sys.stdin); m=[c['id'] for c in d if c['name']=='Distribution Center End-to-End Evaluation v1']; print(m[0] if m else '')"); \
	if [ -n "$$collection_id" ]; then \
		echo "Collection distribution-center-eval-v1 already registered: $$collection_id"; \
	else \
		tmpdir=$$(mktemp -d); \
		tmp="$$tmpdir/evalhub-collection.yaml"; \
		sed "s/__PROVIDER_ID__/$$provider_id/" eval-suite/config/evalhub-collection.yaml > "$$tmp"; \
		evalhub collections create --file "$$tmp"; \
		rm -rf "$$tmpdir"; \
	fi

# Submits a real run of the registered distribution-center-eval-v1
# collection against the in-cluster agent deployment (see
# eval-suite/config/evalhub-job.yaml's header comment for why it must be
# the in-cluster Service DNS, not localhost -- this executes as a k8s Job
# inside the cluster, not on your machine). Does not itself register
# anything; run `make register-eval-suite` first if dc-eval-suite /
# distribution-center-eval-v1 aren't already registered (check the
# OpenShift AI dashboard's Evaluations page, or `evalhub collections list`).
# ARGS defaults to nothing (submit and return immediately -- check progress
# with `evalhub eval status`); pass ARGS="--wait" to block until it
# finishes, or ARGS="--watch" to stream logs.
evalhub-run:
	evalhub config set base_url $(EVALHUB_BASE_URL)
	evalhub config set token $(MLFLOW_TRACKING_TOKEN)
	evalhub config set tenant $(EVALHUB_TENANT)
	evalhub eval run --config eval-suite/config/evalhub-job.yaml $(ARGS)

run-ingest-api:
	cd po-ingest-api && PORT=$(PO_INGEST_API_PORT) python3 -m src

run-local-dc-agent:
	cd local-dc-agent && PORT=$(AGENT_PORT) python3 -m src

run-local-wms-api:
	cd local-wms-api && PORT=$(WMS_API_PORT) python3 -m src

run-local-inventory-robot-api:
	cd local-inventory-robot-api && PORT=$(ROBOT_API_PORT) python3 -m src

run-supervisor-api:
	cd supervisor-api && PORT=$(SUPERVISOR_API_PORT) python3 -m src

run-local-shipping-api:
	cd local-shipping-api && PORT=$(SHIPPING_API_PORT) python3 -m src

run-label-api:
	cd label-api && PORT=$(LABEL_API_PORT) python3 -m src

run-dashboard:
	cd dashboard-ui && PORT=$(DASHBOARD_PORT) python3 -m src

start-all:
	@mkdir -p $(LOG_DIR) $(PID_DIR)
	@echo "Starting all services (logs: $(LOG_DIR)/, pids: $(PID_DIR)/)..."
	@to_check=""; \
	for entry in $(SERVICES); do \
		name=$${entry%%:*}; rest=$${entry#*:}; dir=$${rest%%:*}; portvar=$${rest#*:}; \
		port=$${!portvar}; \
		pidfile="$(CURDIR)/$(PID_DIR)/$$name.pid"; \
		logfile="$(CURDIR)/$(LOG_DIR)/$$name.log"; \
		if [ -f "$$pidfile" ] && kill -0 "$$(cat "$$pidfile")" 2>/dev/null; then \
			echo "  [skip] $$name already running (pid $$(cat "$$pidfile"))"; \
			continue; \
		fi; \
		extra_env=""; \
		if [ "$$name" = "ingest-api" ]; then extra_env="TORCHDYNAMO_DISABLE=1"; fi; \
		(cd $$dir && trap '' HUP && exec env PORT=$$port $$extra_env python3 -m src > "$$logfile" 2>&1) & \
		pid=$$!; \
		echo $$pid > "$$pidfile"; \
		to_check="$$to_check $$name:$$port:$$pid:$$logfile"; \
	done; \
	failed=0; \
	for item in $$to_check; do \
		name=$${item%%:*}; rest=$${item#*:}; \
		port=$${rest%%:*}; rest=$${rest#*:}; \
		pid=$${rest%%:*}; logfile=$${rest#*:}; \
		up=0; \
		for i in $$(seq 1 90); do \
			if ! kill -0 "$$pid" 2>/dev/null; then break; fi; \
			if grep -q 'Uvicorn running on' "$$logfile" 2>/dev/null; then up=1; break; fi; \
			sleep 0.5; \
		done; \
		if [ "$$up" -eq 1 ] && kill -0 "$$pid" 2>/dev/null; then \
			echo "  [ok]   $$name -> http://localhost:$$port (pid $$pid, log $$logfile)"; \
		else \
			rm -f "$(CURDIR)/$(PID_DIR)/$$name.pid"; \
			failed=1; \
			echo "  [FAIL] $$name failed to start -> http://localhost:$$port (log $$logfile)"; \
			sed 's/^/           | /' "$$logfile" | tail -5; \
		fi; \
	done; \
	echo "Done. Tail logs with: tail -f $(LOG_DIR)/*.log"; \
	exit $$failed

kill-all:
	@if [ ! -d $(PID_DIR) ]; then echo "Nothing to kill ($(PID_DIR) not found)."; exit 0; fi
	@for pidfile in $(PID_DIR)/*.pid; do \
		[ -e "$$pidfile" ] || continue; \
		name=$$(basename "$$pidfile" .pid); \
		pid=$$(cat "$$pidfile"); \
		if kill -0 "$$pid" 2>/dev/null; then \
			kill "$$pid" 2>/dev/null && echo "  [killed] $$name (pid $$pid)"; \
		else \
			echo "  [stale]  $$name (pid $$pid not running)"; \
		fi; \
		rm -f "$$pidfile"; \
	done
	@echo "Kills complete.  Sleeping for a couple of seconds."
	sleep 2
	@echo "Manually confirm from the following list that all python instances should actually exist."
	ps -ef | grep python
	@echo "Done."

restart-all: kill-all start-all
	tail -f target/logs/*.log

status-all:
	@if [ ! -d $(PID_DIR) ]; then echo "No services started."; exit 0; fi
	@for pidfile in $(PID_DIR)/*.pid; do \
		[ -e "$$pidfile" ] || continue; \
		name=$$(basename "$$pidfile" .pid); \
		pid=$$(cat "$$pidfile"); \
		if kill -0 "$$pid" 2>/dev/null; then \
			echo "  [up]   $$name (pid $$pid)"; \
		else \
			echo "  [down] $$name (stale pid $$pid)"; \
		fi; \
	done

pipeline:
	@echo "Compiling pipeline."
	cd vision-ml/src && python pipeline.py
	@echo "Done."

clean:
	rm -rf $(TARGET_DIR)

test:
	cd local-dc-agent && PYTHONPATH=src python3 -m pytest tests
	cd local-inventory-robot-api && PYTHONPATH=src python3 -m pytest tests
	cd local-shipping-api && PYTHONPATH=src python3 -m pytest tests
	cd local-wms-api && PYTHONPATH=src python3 -m pytest tests
	cd label-api && PYTHONPATH=src python3 -m pytest tests
	cd po-ingest-api && PYTHONPATH=src python3 -m pytest tests
	cd supervisor-api && PYTHONPATH=src python3 -m pytest tests
	cd eval-suite && PYTHONPATH=src python3 -m pytest tests
