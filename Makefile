VENV := .venv
PARALLEL ?= 1
TESTSUITEFLAGS ?= ""
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CHARMS := token-distributor microovn

build:
	for charm in $(CHARMS); do \
		$(MAKE) -C $$charm; \
	done

$(VENV):
	python3 -m venv $(VENV) --upgrade-deps
	$(PIP) install -r tests/requirements.txt

check-system: $(VENV)
	./$(VENV)/bin/pytest -v -n $(PARALLEL) tests/integration $(TESTSUITEFLAGS)

test: $(VENV) check-system
.PHONY: test

clean:
	$(MAKE) -C token-distributor clean
	$(MAKE) -C microovn clean
