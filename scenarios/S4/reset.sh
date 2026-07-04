#!/usr/bin/env bash
kubectl delete -f "$(dirname "$0")/networkchaos.yaml" --ignore-not-found
