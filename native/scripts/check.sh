#!/usr/bin/env bash
# The checks of the native engine (native/README.md): every build, the tests on both instruction-set
# paths, and the disassembly scan of each build.
#
#   bash native/scripts/check.sh            the goldens must exist (python native/tools/goldens.py native/work/synthetic)
#
# Steps: the release and debug builds of visr-engine, the unit tests, the tests against the Python
# goldens (base and AVX2 paths), the program with VISR_ISA=base, and isa-scan on the release and debug
# programs and on every test binary.
set -euo pipefail
cd "$(dirname "$0")/.."
T="${CARGO_TARGET_DIR:-target}"

echo "== builds (x86-64-v2, native/.cargo/config.toml)"
cargo build --release -p visr-engine -p isa-scan
cargo build -p visr-engine

echo "== tests (release: the goldens, both paths)"
cargo test --release -p visr-engine -p isa-scan -- --nocapture 2>&1 | grep -E '^(case|cusum|    limit|limit cases|test |test result)'

echo "== the switch: VISR_ISA=base"
GOLD="${VISR_GOLDENS:-work/synthetic}"
VISR_ISA=base VISR_PACK="$GOLD/pack" VISR_STATE_DIR="$(mktemp -d)" "$T/release/visr-engine" check | grep 'isa base'

echo "== disassembly scan of each build"
BINS="$T/release/visr-engine $T/debug/visr-engine"
for profile in "" "--release"; do
  BINS="$BINS $(cargo test -p visr-engine --no-run $profile --message-format=json 2>/dev/null \
    | grep -o '"executable":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ')"
done
"$T/release/isa-scan" $BINS
echo "== all checks passed"
