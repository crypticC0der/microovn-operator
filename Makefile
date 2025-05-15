build: microovn-control.charm microovn-worker.charm

microovn-worker.charm: charmcraftworker.yaml src/charm.py
	cp charmcraftworker.yaml charmcraft.yaml
	charmcraft pack
	mv microovn-worker_*.charm microovn-worker.charm 

microovn-control.charm: charmcraftcontrol.yaml src/charm.py
	cp charmcraftcontrol.yaml charmcraft.yaml
	charmcraft pack
	mv microovn-control_*.charm microovn-control.charm 
	
clear:
	rm charmcraft.yaml
	rm *.charm

clean:
	charmcraft clean
	rm charmcraft.yaml
	rm *.charm
