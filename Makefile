# SiliconKnights / ABB Accelerator — build, test, deploy.
# Override image coords:  make import REG=skn TAG=v0.1
.PHONY: help test images import charts demo pause resume clean
REG ?= skn
TAG ?= v0.1

help:
	@echo "make test    - engine (pytest) + aggregator (go) unit tests"
	@echo "make images  - docker build all 15 workloads + aggregator + engine + api + dashboard"
	@echo "make import  - build + import images into K3s containerd (air-gap path)"
	@echo "make charts  - helm lint + template the factory chart"
	@echo "make demo    - ./deploy/skctl up --mode solo (deploy on one box)"
	@echo "make pause / make resume - idle / restore the factory"
	@echo "make clean   - remove pycache + locally-built Go binaries"

test:
	cd correlation && python3 -m pytest tests/ -q
	-cd aggregator && go test ./...

images:
	@for d in workloads/*/; do n=$$(basename $$d); echo ">> build $$n"; docker build -t $(REG)/$$n:$(TAG) $$d || exit 1; done
	docker build -t $(REG)/aggregator:$(TAG) aggregator
	docker build -t $(REG)/correlation-engine:$(TAG) correlation
	docker build -t $(REG)/api:$(TAG) api
	docker build -t $(REG)/dashboard:$(TAG) dashboard

import: images
	@for d in workloads/*/; do n=$$(basename $$d); docker save $(REG)/$$n:$(TAG) | sudo k3s ctr images import -; done
	docker save $(REG)/aggregator:$(TAG) | sudo k3s ctr images import -
	docker save $(REG)/correlation-engine:$(TAG) | sudo k3s ctr images import -
	docker save $(REG)/api:$(TAG) | sudo k3s ctr images import -
	docker save $(REG)/dashboard:$(TAG) | sudo k3s ctr images import -

charts:
	helm lint deploy/charts/factory
	helm template deploy/charts/factory >/dev/null && echo "chart renders OK"

demo:
	./deploy/skctl up --mode solo

pause:
	./deploy/skctl pause

resume:
	./deploy/skctl resume

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
	rm -f aggregator/aggregator workloads/*/ccr workloads/*/dcim-bridge \
	      workloads/*/notify-gateway workloads/*/plc-gateway workloads/*/safety-interlock 2>/dev/null; true
