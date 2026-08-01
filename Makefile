TARGET_DIR := target

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

.PHONY: help install generate-pos run-ingest-api run-local-dc-agent run-local-wms-api run-local-inventory-robot-api clean

help:
	@echo "Targets:"
	@echo "  install                      Install dependencies from every requirements.txt (via: $(PIP))"
	@echo "  generate-pos                 Generate sample PO PDFs into $(TARGET_DIR)/pos (ARGS=\"--count 25\" to pass flags)"
	@echo "  run-ingest-api               Run the PO ingest API (http://localhost:8000)"
	@echo "  run-local-dc-agent           Run the distribution center A2A agent (http://localhost:9100)"
	@echo "  run-local-wms-api            Run the local WMS inventory API (http://localhost:8001)"
	@echo "  run-local-inventory-robot-api Run the local inventory robot API (http://localhost:8002)"
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
	cd po-ingest-api && python3 -m src

run-local-dc-agent:
	cd local-dc-agent && python3 -m src

run-local-wms-api:
	cd local-wms-api && python3 -m src

run-local-inventory-robot-api:
	cd local-inventory-robot-api && python3 -m src

clean:
	rm -rf $(TARGET_DIR)
