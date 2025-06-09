VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CHARMS := token-distributor microovn

build:
	for charm in $(CHARMS); do \
		$(MAKE) -C $$charm; \
	done

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install pytest
	$(PIP) install jubilant

test: $(VENV)
	./$(VENV)/bin/pytest tests

clean:
	$(MAKE) -C token-distributor clean
	$(MAKE) -C microovn clean
