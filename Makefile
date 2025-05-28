CHARMS := token-distributor microovn

build:
	for charm in $(CHARMS); do \
		$(MAKE) -C $$charm; \
	done

clean:
	$(MAKE) -C token-distributor clean
	$(MAKE) -C microovn clean
