.PHONY: help test test-v run lint clean

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PYTEST ?= $(VENV)/bin/pytest

help:
	@echo "Commandes disponibles :"
	@echo "  make test      - Exécute l'ensemble de la suite de tests"
	@echo "  make test-v    - Exécute les tests en mode verbeux (-v)"
	@echo "  make run       - Lance le bot Telegram en local"
	@echo "  make clean     - Nettoie les caches (__pycache__, .pytest_cache)"

test:
	$(PYTEST)

test-v:
	$(PYTEST) -v

run:
	$(PYTHON) app/main.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
