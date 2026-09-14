# VISR (Tata InnoVent): test, build, and import the images for the single-node K3s box.
# Override the image coordinates: make import REG=skn TAG=v0.1
.PHONY: help test images import clean
REG ?= skn
TAG ?= v0.1
# image-name:build-directory pairs. PIVOT_SETUP.md section 4 builds the same set.
IMAGES = aggregator:aggregator correlation-engine:correlation api:api dashboard:dashboard \
         plant-sim:plant openplc:plc tag-server:scada

help:
	@echo "make test    - pytest (correlation, plant, api, scada) + aggregator go test"
	@echo "make images  - docker build every VISR image (openplc is a slow source build)"
	@echo "make import  - build, then import every image into K3s containerd"
	@echo "make clean   - remove Python caches and the local aggregator binary"

test:
	cd correlation && python3 -m pytest -q
	cd plant && python3 -m pytest -q
	cd api && python3 -m pytest -q
	cd scada && python3 -m pytest -q
	-cd aggregator && go test ./...

images:
	@for p in $(IMAGES); do n=$${p%%:*}; d=$${p#*:}; echo ">> build $(REG)/$$n:$(TAG) from $$d/"; docker build -t $(REG)/$$n:$(TAG) $$d || exit 1; done

import: images
	@for p in $(IMAGES); do n=$${p%%:*}; docker save $(REG)/$$n:$(TAG) | sudo k3s ctr images import - || exit 1; done

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
	rm -f aggregator/aggregator
