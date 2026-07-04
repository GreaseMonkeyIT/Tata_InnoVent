#!/usr/bin/env bash
kubectl apply -f "$(dirname "$0")/networkchaos.yaml"
echo "S4 fired - 200ms±50 on notify-gateway for 3m; watch alert-dispatcher retry amplification"
