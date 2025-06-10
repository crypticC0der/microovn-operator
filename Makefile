VENV := .venv
PARALLEL ?= 1
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

test: $(VENV)
	./$(VENV)/bin/pytest -v -n $(PARALLEL) tests

clean:
	$(MAKE) -C token-distributor clean
	$(MAKE) -C microovn clean
