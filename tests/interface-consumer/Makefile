CHARMFILE := interface-consumer_amd64.charm
build: $(CHARMFILE)

lib/charms/microovn/v0/ovsdb.py: ../../microovn/lib/charms/microovn/v0/ovsdb.py
	mkdir -p lib/charms/microovn/
	cp -r ../../microovn/lib/charms/microovn/v0/ lib/charms/microovn/

lib/charms/tls_certificates_interface/v4/tls_certificates.py: ../../microovn/lib/charms/tls_certificates_interface/v4/tls_certificates.py
	mkdir -p lib/charms/tls_certificates_interface
	cp -r ../../microovn/lib/charms/tls_certificates_interface/v4/ lib/charms/tls_certificates_interface/

$(CHARMFILE): lib/charms/microovn/v0/ovsdb.py lib/charms/tls_certificates_interface/v4/tls_certificates.py src/charm.py charmcraft.yaml
	charmcraft pack

clean:
	rm -rf lib/charms/microovn/v0
	rm -rf lib/charms/tls_certificates_interface/v4
	charmcraft clean
