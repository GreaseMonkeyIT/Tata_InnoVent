#!/usr/bin/env bash
# S5 - memory leak -> OOM loop. Engine must FORECAST the OOM before the kernel kills it.
kubectl set env deploy/vision-qc -n factory-edge LEAK_ENABLED=true
echo "S5 fired - ~6MB/s leak toward the 512Mi limit; expect OOMKilled in ~80s, restarts after"
