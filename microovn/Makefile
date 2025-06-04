DIRNAME := $(shell basename $(CURDIR))
CHARMFILE := microovn_amd64.charm

build: $(CHARMFILE)

$(CHARMFILE): src/charm.py charmcraft.yaml
	charmcraft pack -v

clean:
	charmcraft clean
	rm $(CHARMFILE) -f
