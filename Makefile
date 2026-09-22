# VISR (Tata InnoVent): test, build, and push the images for the single-node K3s box.
# The box runs a local registry on 127.0.0.1:5000 (PIVOT_SETUP.md step 4). The manifests pull
# localhost:5000/skn/<name>:v0.1 with imagePullPolicy Always, so `make push` plus a rollout
# restart deploys without sudo. Override: make push REGISTRY=localhost:5000 TAG=v0.1
.PHONY: help test images push import clean
REG ?= skn
TAG ?= v0.1
REGISTRY ?= localhost:5000
# image-name:build-directory pairs. PIVOT_SETUP.md section 4 builds the same set.
IMAGES = aggregator:aggregator correlation-engine:correlation api:api dashboard:dashboard \
         plant-sim:plant openplc:plc tag-server:scada vplc:vplc
# `make push ONLY="api dashboard"` builds and pushes only those images.
ONLY ?=

help:
	@echo "make test    - pytest (correlation, plant, api, scada, vplc) + aggregator go test"
	@echo "make images  - docker build every VISR image (openplc is a slow source build)"
	@echo "make push    - build, then push every image to the box registry (no sudo)"
	@echo "make import  - fallback without the registry: import into K3s containerd (sudo)"
	@echo "make clean   - remove Python caches and the local aggregator binary"

test:
	cd correlation && python3 -m pytest -q
	cd plant && python3 -m pytest -q
	cd api && python3 -m pytest -q
	cd scada && python3 -m pytest -q
	cd vplc && python3 -m pytest -q
	-cd aggregator && go test ./...

images:
	@for p in $(IMAGES); do n=$${p%%:*}; d=$${p#*:}; \
	  if [ -n "$(ONLY)" ] && ! echo " $(ONLY) " | grep -q " $$n "; then continue; fi; \
	  echo ">> build $(REG)/$$n:$(TAG) from $$d/"; docker build -t $(REG)/$$n:$(TAG) $$d || exit 1; done

push: images
	@for p in $(IMAGES); do n=$${p%%:*}; \
	  if [ -n "$(ONLY)" ] && ! echo " $(ONLY) " | grep -q " $$n "; then continue; fi; \
	  docker tag $(REG)/$$n:$(TAG) $(REGISTRY)/$(REG)/$$n:$(TAG) && docker push -q $(REGISTRY)/$(REG)/$$n:$(TAG) || exit 1; done

import: images
	@for p in $(IMAGES); do n=$${p%%:*}; docker save $(REG)/$$n:$(TAG) | sudo k3s ctr images import - || exit 1; done

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
	rm -f aggregator/aggregator
