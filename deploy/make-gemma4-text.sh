#!/usr/bin/env bash
# Make gemma4-text:e4b on forge: gemma4:e4b-it-qat without its vision projector.
#   bash make-gemma4-text.sh            (source gemma4:e4b-it-qat, name gemma4-text:e4b)
#   bash make-gemma4-text.sh SRC NAME
# Without the projector, the llama-server fit does not reserve GPU memory for it, so Ollama puts 41 of 43
# layers on the 4 GB GPU, not 15. The copy uses the same weight files, so it needs almost no disk space.
# The copy cannot read images. The VISR narrator and the forgemon monitor send text only.
# The api asks for this name (OLLAMA_MODEL in deploy/api.yaml).
set -euo pipefail
src=${1:-gemma4:e4b-it-qat}
name=${2:-gemma4-text:e4b}
models=${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}
manifest="$models/manifests/registry.ollama.ai/library/${src%%:*}/${src#*:}"
[ -r "$manifest" ] || { echo "no manifest for $src at $manifest (run: ollama pull $src)" >&2; exit 1; }
proj=$(python3 -c 'import json,sys
m = json.load(open(sys.argv[1]))
print(" ".join(l["digest"][7:] for l in m["layers"] if l["mediaType"].endswith(".projector")))' "$manifest")
mf=$(mktemp)
trap 'rm -f "$mf"' EXIT
if [ -n "$proj" ]; then
  ollama show --modelfile "$src" | grep -v -e "$proj" > "$mf"
else
  ollama show --modelfile "$src" > "$mf"
fi
grep -c '^FROM' "$mf" | xargs echo "FROM lines in the new Modelfile:"
ollama create "$name" -f "$mf"
ollama show "$name" | sed -n '1,12p'
