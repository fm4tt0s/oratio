# Detect the right Python binary.
# Prefer python3.13, fall back to python3.12, then python3.
# Override at the command line: make install PYTHON=python3.11
PYTHON := $(shell command -v python3.13 2>/dev/null || \
                  command -v python3.12 2>/dev/null || \
                  command -v python3 2>/dev/null)

VENV   := .venv
BIN    := $(VENV)/bin
PY     := $(BIN)/python
PIP    := $(BIN)/pip

.DEFAULT_GOAL := help

# ── Setup ─────────────────────────────────────────────────────────────────────

.PHONY: install
install: $(VENV)/bin/activate  ## Create venv and install dependencies
	@echo "Setup complete. Python: $(shell $(PY) --version)"

$(VENV)/bin/activate:
	@echo "Creating venv with: $(PYTHON)"
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# ── Run ───────────────────────────────────────────────────────────────────────

.PHONY: test
test: $(VENV)/bin/activate  ## Run the RAG self-test (needs .env configured)
	$(PY) rag.py

.PHONY: server
server: $(VENV)/bin/activate  ## Start the MCP server (Claude Desktop / VS Code MCP)
	$(PY) server.py

.PHONY: server-http
server-http: $(VENV)/bin/activate  ## Start the HTTP server (VS Code extension)
	$(PY) server_http.py

.PHONY: skill
skill: $(VENV)/bin/activate  ## Generate a Claude.ai Project system prompt from your PDF
	$(PY) generate_skill.py

# ── Maintenance ───────────────────────────────────────────────────────────────

.PHONY: clean
clean:  ## Remove the virtual environment
	rm -rf $(VENV)

.PHONY: reinstall
reinstall: clean install  ## Clean and reinstall everything

# ── Help ──────────────────────────────────────────────────────────────────────

.PHONY: help
help:  ## Show available commands
	@echo "Detected Python: $(PYTHON)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
