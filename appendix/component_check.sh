#!/usr/bin/env bash
# component_check.sh - per-component P0->P2 integrity sweep (LOG-027). Read-only. Run on the box.
#   bash appendix/component_check.sh
set -u   # NOT pipefail: `kubectl ... | grep -q` trips SIGPIPE under pipefail -> false FAIL (cadvisor)
HERE="$(cd "$(dirname "$0")" && pwd)"
P=0; F=0
ok(){ printf '  \033[32mOK\033[0m %s\n' "$1"; P=$((P+1)); }
no(){ printf '  \033[31mXX\033[0m %s\n' "$1"; F=$((F+1)); }
hd(){ printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

hd "P0 - kernel / K3s / PSI gate (LOG-020)"
[ -f /sys/kernel/btf/vmlinux ] && ok "BTF present (eBPF CO-RE)" || no "no /sys/kernel/btf/vmlinux"
[ "$(stat -fc %T /sys/fs/cgroup 2>/dev/null)" = cgroup2fs ] && ok "cgroup v2" || no "not cgroup2fs"
grep -q . /proc/pressure/cpu 2>/dev/null && ok "PSI active (/proc/pressure/cpu)" || no "PSI off"
kubectl get nodes 2>/dev/null | grep -q ' Ready' && ok "node Ready" || no "node not Ready"
kubectl get sc 2>/dev/null | grep -qi 'local-path.*default' && ok "local-path = default StorageClass" || no "local-path not default SC"
NODE=$(kubectl get no -o name 2>/dev/null | head -1 | cut -d/ -f2)
kubectl get --raw "/api/v1/nodes/$NODE/proxy/metrics/cadvisor" 2>/dev/null | grep -q container_pressure && ok "kubelet cadvisor serves PSI" || no "cadvisor has no container_pressure"

hd "P1 - 15 factory workloads (LOG-023)"
n=$(kubectl get pods -A 2>/dev/null | grep -c '^factory-')
[ "$n" -ge 13 ] 2>/dev/null && ok "factory pods present: $n" || no "only $n factory pods (expect >=13)"
bad=$(kubectl get pods -A 2>/dev/null | awk '/^factory-/ && ($5+0)>0 {print $2"("$5")"}' | tr '\n' ' ')
[ -z "$bad" ] && ok "zero unplanned restarts" || no "restarts: $bad"
kubectl get pvc -n factory-data 2>/dev/null | grep -q 'tsdb-pvc.*Bound' && ok "tsdb-pvc Bound" || no "tsdb-pvc not Bound"
kubectl get pvc -n factory-data 2>/dev/null | grep -q 'shared-logs-pvc.*Bound' && ok "shared-logs-pvc Bound" || no "shared-logs-pvc not Bound"

hd "P2 - telemetry taps (delegates to verify_taps.sh)"
if [ -f "$HERE/verify_taps.sh" ]; then bash "$HERE/verify_taps.sh" 2>/dev/null | tail -5; else no "verify_taps.sh missing"; fi

hd "L2 aggregator (P3 - if deployed)"
if kubectl get pods -A 2>/dev/null | grep -qi aggregator; then ok "aggregator pod present"; else echo "  -- not deployed yet (expected; P3 deploy step)"; fi

hd "Summary"; printf "  component-level OK=%d  XX=%d  (P2 tap detail above)\n" "$P" "$F"
[ "$F" -eq 0 ] && echo "  -> P0/P1 components intact" || echo "  -> investigate the XX lines"
