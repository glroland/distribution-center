TARGET_DIR := target

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
	-name 'requirements.txt')

.PHONY: help install generate-pos run-ingest-api run-local-dc-agent run-local-wms-api run-local-inventory-robot-api run-supervisor-api run-local-shipping-api run-dashboard clean

help:
	@echo "Targets:"
	@echo "  install                      Install dependencies from every requirements.txt (via: $(PIP))"
	@echo "  generate-pos                 Generate sample PO PDFs into $(TARGET_DIR)/pos (ARGS=\"--count 25\" to pass flags)"
	@echo "  run-ingest-api               Run the PO ingest API (http://localhost:8000)"
	@echo "  run-local-dc-agent           Run the distribution center A2A agent (http://localhost:9100)"
	@echo "  run-local-wms-api            Run the local WMS inventory API (http://localhost:8001)"
	@echo "  run-local-inventory-robot-api Run the local inventory robot API (http://localhost:8002)"
	@echo "  run-supervisor-api           Run the local supervisor API (http://localhost:8003)"
	@echo "  run-local-shipping-api       Run the local shipping API (http://localhost:8004)"
	@echo "  run-dashboard                Run the demo control room UI (http://localhost:8090)"
	@echo "  clean                        Remove the $(TARGET_DIR) directory"

install:
	@echo "Using: $(PIP)"
	@for req in $(REQUIREMENTS); do \
		echo "==> $$req"; \
		$(PIP) -r "$$req" || exit 1; \
	done

generate-pos:
	@mkdir -p $(TARGET_DIR)/pos
	cd test-po-generator && python3 -m src --output-dir "../$(TARGET_DIR)/pos" $(ARGS)

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

run-dashboard:
	cd dashboard-api && PORT=$(DASHBOARD_PORT) python3 -m src

clean:
	rm -rf $(TARGET_DIR)
