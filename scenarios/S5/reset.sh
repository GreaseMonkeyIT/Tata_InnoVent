#!/usr/bin/env bash
kubectl set env deploy/vision-qc -n factory-edge LEAK_ENABLED=false
kubectl rollout restart deploy/vision-qc -n factory-edge
