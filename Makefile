PY?=python3
VENV?=.venv
PIP=$(VENV)/bin/pip
PYTHON=$(VENV)/bin/python
RUFF=$(VENV)/bin/ruff

.PHONY: venv install dev-install run lint fix freeze clean

venv:
	$(PY) -m venv $(VENV)

install: venv
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt
