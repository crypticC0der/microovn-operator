lib/charms/microovn/v0/ovsdb.py: ../../microovn/lib/charms/microovn/v0/ovsdb.py
	mkdir -p lib/charms/microovn/
	cp -r ../../microovn/lib/charms/microovn/v0/ lib/charms/microovn/

build: lib/charms/microovn/v0/ovsdb.py src/charm.py charmcraft.yaml
	charmcraft pack

clean:
	rm -r lib/charms/ovsdb_interface/v0
	charmcraft clean
