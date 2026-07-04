#!/usr/bin/env bash
# S3 - CPU burst + throttle interference: run the 5-min rollup NOW.
kubectl create job --from=cronjob/analytics-batch s3-run-$(date +%s) -n factory-data
echo "S3 fired - 2-core demand under 500m limit; watch CFS throttle + CPU PSI on co-residents (no network edge!)"
