#!/bin/sh
# Seed ~400MB of "firmware blobs" into tmpfs: big RAM that is USAGE, not PRESSURE.
mkdir -p /cache && i=0
while [ $i -lt 4 ]; do dd if=/dev/urandom of=/cache/fw-$i.bin bs=1M count=100 2>/dev/null; i=$((i+1)); done
exec nginx -g 'daemon off;'
