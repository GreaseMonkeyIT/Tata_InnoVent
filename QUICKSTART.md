# QUICKSTART — clone to a running causal-AIOps demo on a fresh Linux PC

End state: the 15-pod factory + telemetry + L2 aggregator + L3 correlation engine running on
single-node K3s, and `S1` producing a causal graph at the engine's `/graph`. ~30 min, most of
it image builds.

> Repo: `https://github.com/GreaseMonkeyIT/ABB_Accelerator_Submission`

---

## 0. Host requirements (the hard gate)

A **real Linux kernel** — bare metal or a full VM. **WSL2 will not work** (no per-pod PSI).
Ubuntu / Xubuntu 24.04+ is the tested base; 16 GB RAM; ~64 GB free disk for the 14-day
TimescaleDB window (less if you shorten retention). Confirm all five before going further:

```bash
uname -r                         # >= 5.15  (24.04 ships 6.8+)
ls /sys/kernel/btf/vmlinux       # exists      (eBPF CO-RE)
stat -fc %T /sys/fs/cgroup       # cgroup2fs
cat /proc/pressure/cpu           # 'some'/'full' lines present  (PSI is on)
timedatectl | grep synchronized  # yes         (the lag engine needs a synced clock)
```

If `/proc/pressure` is missing, the kernel booted with `psi=0` — check `cat /proc/cmdline`,
remove it, `sudo update-grub`, reboot.

## 1. Toolchain

```bash
sudo apt update
sudo apt install -y docker.io git make curl
sudo usermod -aG docker $USER && newgrp docker        # so docker runs without sudo
sudo snap install helm --classic
```

## 2. K3s with the PSI feature gate (do not skip the kubelet-arg)

```bash
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable traefik --kubelet-arg=feature-gates=KubeletPSI=true" sh -

mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config && sudo chown $USER ~/.kube/config
echo 'export KUBECONFIG=$HOME/.kube/config' >> ~/.bashrc && export KUBECONFIG=$HOME/.kube/config
kubectl get nodes                                     # Ready

# prove per-pod PSI is scrapeable (this is the whole differentiator):
kubectl get --raw "/api/v1/nodes/$(kubectl get no -o name | cut -d/ -f2)/proxy/metrics/cadvisor" | grep -m1 container_pressure
```

## 3. Clone, build, deploy

```bash
git clone https://github.com/GreaseMonkeyIT/ABB_Accelerator_Submission.git
cd ABB_Accelerator_Submission
chmod +x deploy/skctl appendix/*.sh scenarios/*/*.sh   # restore exec bits (harmless if already set)

make import                 # docker build 15 workloads + aggregator + engine + api + dashboard, import into k3s containerd
./deploy/skctl up --mode solo   # deploy factory + telemetry + L2 + L3
```

`make test` (engine pytest + aggregator `go test`) is an optional pre-build sanity check; it
needs `python3` + `numpy/scipy/networkx` and `go` on the host, which the images otherwise carry.

### Single-disk box — one edit

The committed chart pins the two factory PVCs to a `slowdisk` StorageClass (a dedicated HDD on
the reference box, see `deploy/slowdisk.yaml`). On a normal single-disk PC, point them at the
default provisioner before `skctl up`: in `deploy/charts/factory/values.yaml`, under `pvcs:`,
change both `storageClass: slowdisk` to `storageClass: local-path` (or delete the key — it
defaults to `local-path`).

## 4. Verify it came up

```bash
kubectl get pods -A | grep -vE 'Running|Completed'    # ideally only the header line
bash appendix/component_check.sh                      # P0-P2 per-component sweep
bash appendix/verify_taps.sh                          # telemetry taps (add --strict once eBPF collectors are installed)
kubectl get pvc -n factory-data                       # tsdb-pvc + shared-logs-pvc -> Bound
```

Then give a cold deploy **~15–20 minutes**: TimescaleDB populates, the aggregator's 15-min ring fills, and the engine learns each pod's steady-state PSI baseline. Detection is deviation-based, so it stays silent until it has a baseline — during warm-up `/graph` shows transient TimescaleDB I/O findings that clear on their own to `findings: []` (S0). A warm redeploy (engine-memory kept) is ready in ~5 min.

## 5. Fire a scenario and read the causal graph

```bash
bash scenarios/S1/trigger.sh                          # S1: sustained fio storm on the shared disk
sleep 50
kubectl get --raw "/api/v1/namespaces/aiops/services/correlation-engine:9100/proxy/graph" | python3 -m json.tool
```

Expect a JSON graph with: `root_cause_ranking[0] = cooling-monitor`, a hot edge `cooling-monitor ->
timescaledb` carrying `evidence` like `["write","pvc","temporal"]` (per-pod **write** = the source
attribution + shared-disk topology + time order — **no resource thresholds in the causal path**), and a
`blast_radius`. The engine searches the ring for the disturbance, so reading `/graph` ~50s in (mid-storm)
shows it clearly; on a settled engine S1 lights up ~45–50s after the trigger.

## Operating notes

- `./deploy/skctl up --mode solo` is idempotent — re-run after any chart/image change. In solo
  mode never pass `--components <subset>` (it disables the unlisted groups; decision D-012).
- `./deploy/skctl pause` / `resume` idles the factory between sessions (PVCs + telemetry kept).
- Heavy load is on-demand only: S1 via `scenarios/S1/trigger.sh` (or `POST :8080/flush` to
  cooling-monitor); S2/S3 cronjobs are suspended and fire via their `trigger.sh`.
- After any code change to a workload/aggregator/engine, rebuild that image and
  `k3s ctr images import` — sources are baked into the images (`make import` does all of them).
- Tuning knobs that need no rebuild: engine `ANALYSIS_WINDOW` (correlation span, env on
  `deploy/correlation-engine`); cooling-monitor `FIO_JOBS/RUNTIME/FSYNC` (Helm values).

See `README.md` for architecture and `MASTER_PLAN.md` for the design decisions and methodology.
