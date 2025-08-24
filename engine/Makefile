# ================================================================
#  Makefile — MCP Demo Project
#
#  Description:
#    Provides a simple build/management workflow for the MCP demo.
#    Handles virtual environment setup, dependency installation,
#    removal, and running the MCP engine with arguments.
#
#  Usage:
#    make install             Create venv and install dependencies
#    make uninstall           Remove venv
#    make run ARGS="..."      Run engine/mcp.py inside venv
#
#  Notes:
#    - Requires Python >= 3.9
#    - Virtual environment is always created at .venv/
#    - If .venv already exists, install will fail (use uninstall first)
# ================================================================

PYTHON   := python3
VENV_DIR := .venv
REQ_PKGS := aiohttp colorama
ENGINE   := mcp.py

.PHONY: all install uninstall check-python run

all:
	@echo "\nUsage:"
	@echo "  make install          Create venv and install dependencies"
	@echo "  make uninstall        Remove venv"
	@echo "  make run              Run mcp.py inside venv"
	@echo ""

check-python:
	@echo "\nChecking if we have Python.."
	@command -v $(PYTHON) >/dev/null 2>&1 || \
		{ echo "Error: python3 not found in PATH"; exit 1; }
	@ver=$$($(PYTHON) -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'); \
	major=$$(echo $$ver | cut -d. -f1); \
	minor=$$(echo $$ver | cut -d. -f2); \
	if [ $$major -lt 3 ] || { [ $$major -eq 3 ] && [ $$minor -lt 9 ]; }; then \
	  echo "Error: Python >= 3.9 required (found $$ver)"; exit 1; \
	else \
	  echo "Found Python $$ver (OK)"; \
	fi

install: check-python
	@if [ -d "$(VENV_DIR)" ]; then \
	  echo "Error: virtual environment already exists at $(VENV_DIR)"; \
	  exit 1; \
	fi
	@echo "Creating virtual environment at $(VENV_DIR)"
	@$(PYTHON) -m venv $(VENV_DIR)
	@echo "Upgrading pip..."
	@$(VENV_DIR)/bin/pip install --upgrade pip
	@echo "Installing required packages: $(REQ_PKGS)"
	@$(VENV_DIR)/bin/pip install $(REQ_PKGS)
	@echo ""
	@echo "Installation complete."

uninstall:
	@echo "\nRemoving virtual environment.."
	@if [ -d "$(VENV_DIR)" ]; then \
	  echo "Removing virtual environment at $(VENV_DIR)"; \
	  rm -rf $(VENV_DIR); \
	else \
	  echo "No virtual environment found at $(VENV_DIR)"; \
	fi

run:
	@echo ""
	@if [ ! -d "$(VENV_DIR)" ]; then \
	  echo "No virtual environment found at $(VENV_DIR)"; \
	  echo "Please run: make install"; \
	  exit 1; \
	fi
	@$(VENV_DIR)/bin/python $(ENGINE) --project ../project/mcp_demo.json
