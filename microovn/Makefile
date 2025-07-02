DIRNAME := $(shell basename $(CURDIR))
CHARMFILE := microovn_amd64.charm
OVSDBLIB := lib/charms/microovn/v0/ovsdb.py
TOKENDISTLIB := lib/charms/microcluster_token_distributor/v0/token_distributor.py

build: $(CHARMFILE)

$(CHARMFILE): src/charm.py charmcraft.yaml metadata.yaml $(OVSDBLIB) $(TOKENDISTLIB)
	charmcraft pack -v

clean:
	charmcraft clean
	rm $(CHARMFILE) -f
