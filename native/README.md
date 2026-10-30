# native: the engine, the twin and the console as native programs

The operator's decision of 2026-10-22: the plant stays in Python. The engine, the twin and the console
become native programs, Rust for the engine and Tauri for the console. The engine is one program,
`visr-engine`. All of this work lives in `native/` and changes nothing in the overhaul code.

| Part | What | Python reference | Step |
|---|---|---|---|
| pack | reads the model pack | `build_pack` in `live/engine.py` | N1 (done) |
| node | filters, network, coverage, z, the CUSUMs on the mean and the variance, confirmation, change point | `StreamNode.step` in `live/engine.py` | N1 (done) |
| engine | all nodes for each sample, the incidents, the graph rule | `Engine.step`, `Engine._name` | N2 |
| twin | the free and the reset replays over a window | `twin/twin.py` | N3 |
| cf | the trouble, the removed trouble, the target scores, the root | `eval/cf.py` | N3 |
| source, serve, narrate | the plant service and the PLC layer, HTTP and `/healthz`, the narrator | | later |

## Rules

1. Python is the reference. Each native part gives the numbers of its Python part, and a test proves
   it. The numbers are equal to rounding, not bit for bit: numpy's matrix library and FMA round
   differently. A CUSUM sum at its limit can move an alarm by one sample. The test counts and reports
   each such case.
2. The native engine never imports Python. It connects to the plant service now, and to the PLC layer
   later.
3. All constants go in the model pack: the filter time constants, k, h, the coverage margin, the
   windows and the hold time.
4. Each PC reads its inputs from the plant side directly, never through another VISR PC.
5. The engine uses the time stamp of each sample from its source, never the clock of the PC.
6. The native work makes no edit in the overhaul code.

Numbers are float64 in every part. This is the plan's proposal and it is still open. The test against
Python needs float64, and float32 loses the slow filters and the long CUSUM sums.

## Layout

| Path | What |
|---|---|
| `Cargo.toml` | the workspace: `visr-engine`, `isa-scan` |
| `.cargo/config.toml` | the base build: `target-cpu=x86-64-v2` |
| `visr-engine/` | the engine: `src/` (the library and the program), `tests/goldens.rs` (the test against Python) |
| `isa-scan/` | the disassembly scan of a build |
| `console/` | the console, Tauri. It has its own workspace and lock file, so the engine builds without the webview libraries |
| `tools/export_pack.py` | the live pack (pickle) to the neutral pack |
| `tools/goldens.py` | the synthetic cases, the Python engine's expected values, the pack |
| `scripts/check.sh` | every build, the tests and the scan |
| `work/` | what the tools write: packs, goldens (gitignored: data never goes into git) |

## Build

Rust 1.82 or later (tested with 1.97). From `native/`:

```
cargo build --release -p visr-engine        # target/release/visr-engine
```

The build has no BLAS and no Python. Its linear algebra is Rust only: `ndarray` holds the arrays, and
the products are the engine's own kernels (`isa.rs`). `matrixmultiply` comes with `ndarray`; N1 does
not call it (see N3).

The console (step N0: an empty window) needs the webview packages on Linux. On Ubuntu 24.04:
`libwebkit2gtk-4.1-dev build-essential file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev`.

```
cd console/src-tauri && cargo build --release      # target/release/visr-console, about 5 min the first time
```

## Run

`visr-engine` is ready for a container. It reads its settings from a file and from the environment
(the environment wins), keeps its state in one folder, and logs to standard output, one line per event
(`level=info msg="..."`).

```toml
# engine.toml
pack = "/pack"            # the pack folder (export_pack.py), mounted read-only
state_dir = "/state"      # the engine's state, one folder (a volume)
isa = "auto"              # auto | base | avx2
```

| Variable | Setting |
|---|---|
| `VISR_SETTINGS` | the settings file |
| `VISR_PACK` | `pack` |
| `VISR_STATE_DIR` | `state_dir` |
| `VISR_ISA` | `isa` |

```
visr-engine --settings engine.toml check
```

In step N1 the program reads its settings, selects its instruction set, loads the pack and checks
every hash and shape, and takes over the node states under `state_dir/nodes/<node>/`. A state of
another node or another pack is refused. The source, the HTTP service with `/healthz` and the
incidents come in N2.

## Instruction sets

The base build is x86-64-v2: SSE4.2, no AVX. The kernels that gain from wider vectors have a second
version in `visr_engine::isa::avx2`, which is compiled for AVX2 and FMA. The engine selects it at run
time when the processor has both. There is no AVX-512 path. `isa = "base"` (or `VISR_ISA=base`)
forces the base path, and `isa = "avx2"` fails on a processor without AVX2 or FMA. The two paths round
differently, because FMA rounds a product and a sum once, so the test checks both against Python.

In N1 the only such kernel is the dense layer of the network (`out = x W`). The base path multiplies
and adds, rounding each. The AVX2 path does four outputs at a time with FMA.

`isa-scan` disassembles a build with objdump and fails it when:

- an AVX instruction is anywhere outside `visr_engine::isa::avx2` (AVX here means the VEX mnemonics
  and the ymm registers);
- an AVX-512 instruction is anywhere at all (zmm, the k mask registers, xmm16 to xmm31 and ymm16 to
  ymm31, embedded broadcasts).

`scripts/check.sh` scans each build: the release and debug programs, and every test binary.

The scan found one AVX2 path that was not the engine's. `serde_json`'s `std` feature turns on
`memchr`'s own run-time AVX2 search, and the base switch could not turn that off. The engine now uses
`serde_json` with `alloc` only. Its `memchr` is then built for the base target and has no AVX2 path.

## The model pack

`tools/export_pack.py` writes the neutral pack from a live pack, the `live-pack.pkl` that `python -m
live.engine pack eval/results/<campaign>` writes:

```
python native/tools/export_pack.py eval/results/<campaign>/live-pack.pkl native/work/<campaign>/pack
```

The pack is one folder: `manifest.json` and one `.npy` file for each array. The manifest's `files`
lists every file with its SHA-256. The engine checks every hash when it opens the pack and again when
it reads a file, and it never reads a file the manifest does not list. The hash of `manifest.json`
names the pack, and a node state carries it.

| Manifest key | What |
|---|---|
| `format`, `version`, `float` | `visr-pack`, 1, `float64` |
| `campaign`, `source`, `meta`, `calib_windows` | the campaign, the pickle and its SHA-256, the campaign's metadata |
| `constants` | `sample_s`, `var_win_s`, `coverage_margin`, `lead_s`, `post_s`, `hold_s`, `keep_s`, `graph_windows_s`. The export reads them from `eval/nodes.py` and `live/engine.py`. The coverage margin is a literal in `StreamNode.step`, and the export stops if the source no longer has it |
| `wiring` | the documents |
| `nodes.<name>` | `rule` (`cusum`, `cusumk`, `cusumkv`), the inputs in order (`num_in`, `cats` with their categories, `delta_in`, `counter_resets`), `state_cmd`, the outputs (`out_names`, `out_cats`), `clip`, `confirm_s`, `var_steady`, `h`, `hv`, `sd_bins`. `arrays`: `k`, `kv` (one value per output), `sd_all`, `sd` per PLC state, `var_d`, `var_skip`. `model`: `inputs`, `outputs`, `taus`, `hidden`, the `mlp` layers and the `linear` path (`w`, `b`), `norm` (`xm`, `xs`, `ym`, `ys`, `xlo`, `xhi`), `cov_cols`. `info`: the training settings, for the record |
| `sig2` | the free-replay variance per node (for cf, N3) |
| `files` | the SHA-256 of every array file |

The export stops on a node field it does not know, and so does the engine on a manifest key it does
not know. One example is the one-poll spike rule of the eval-bench branch (`spike`, `h_spike`). The
native engine must never drop part of a model without a word.

## The node state

The state of a node between two samples has one portable form. It uses the same neutral folder
(`format: visr-node-state`) and the names of `StreamNode`:

- the filters;
- the CUSUM cumulative sums and running minimums (`c`, `mn`, `cv`, `mv`);
- the rolling window (`rs`, `rc`, and `hist_rs` and `hist_rc`, up to 61 rows);
- the change-point trackers (`lz`, `up`, `above`);
- the latches, the confirmation count, and the PLC state and its last change;
- the last value of each change input.

The manifest names the node and the pack, so a state can pass from Python to the native engine (the
test does this) and from one PC to another at a takeover.

## The tests

```
python native/tools/goldens.py native/work/synthetic     # Python 3.11 and correlation/requirements.txt, as CONTRIBUTING.md
bash native/scripts/check.sh
```

`goldens.py` has no saved campaign, so it makes small synthetic runs. It trains `eval.nodes.Node` on
them, as `eval/tests/test_nodes.py` does, puts the nodes in a live pack in the form of `build_pack`,
and exports it. It then loads the nodes back as the live engine does (`load_pack`), streams each
scored run through `live.engine.StreamNode`, and writes the following for each case:

- the input of every sample;
- the expected features, z, sums, latches, coverage, alarm, change point and crossings;
- the state after the middle sample.

There are seven cases, each 2,400 scored samples and each one node of the pack:

| Case | Rule | Covers |
|---|---|---|
| `cusumk_text_gap` | cusumk | a text PLC state that selects the residual spread, a text output, a 30 s gap in the meter, a slow shift |
| `cusumkv_text_gap` | cusumkv | the same, with extra noise for the variance statistic, and a k that differs per output |
| `cusumkv_confirm` | cusumkv | a confirmation time of 5 s (4 alarm episodes) |
| `cusumkv_cmd_delta` | cusumkv | `--cmd-delta`: a PLC counter as its change, with a reset |
| `cusumkv_loops_clip` | cusumkv | own-loop inputs left out of coverage, an own-loop output left out of the variance statistic, the steady mask, a clip, a state the training never saw, missing state values |
| `cusumk_input_gap` | cusumk | 10 s without an input: its filters stay NaN, and the node no longer knows |
| `cusum_plain` | cusum | one k for every output |

`tests/goldens.rs` runs every case on the base path and on the AVX2 path. It passes when:

- the features, z and the sums equal Python's to a relative 1e-9 (relative to the value, or to 1 below
  1, since z and the sums are in units of the residual spread);
- the alarms, the coverage, the change points and the crossings are equal sample for sample.

A difference counts as a limit case only where both values agree and sit at the limit: a sum within
rounding of its limit or of 0, or a feature on a coverage bound. Each limit case is reported, and
every other difference fails. The test also checks that:

- the native state after the middle sample equals Python's;
- the native node takes over Python's state and goes on with Python's numbers;
- its own state, saved and read back, goes on bit for bit.

A second test makes a limit case and checks that it is counted, not failed.

Results (2026-10-30, this sandbox: an Intel Xeon with AVX-512, which the engine does not use):

| Case | Path | x | z | s_m | s_v | Alarms equal | Limit cases | State, takeover |
|---|---|---|---|---|---|---|---|---|
| cusum_plain | base / avx2 | 0 / 0 | 8.7e-15 / 7.0e-15 | 5.7e-14 / 5.7e-14 | - | 2400 / 2400 | 0 | equal |
| cusumk_input_gap | base / avx2 | 0 / 0 | 8.8e-15 / 8.8e-15 | 5.7e-14 / 5.7e-14 | - | 2400 / 2400 | 0 | equal |
| cusumk_text_gap | base / avx2 | 0 / 0 | 4.1e-14 / 4.1e-14 | 2.3e-13 / 2.3e-13 | - | 2400 / 2400 | 0 | equal |
| cusumkv_cmd_delta | base / avx2 | 0 / 0 | 5.4e-14 / 5.4e-14 | 1.1e-13 / 1.4e-13 | 9.1e-13 / 3.4e-13 | 2400 / 2400 | 0 | equal |
| cusumkv_confirm | base / avx2 | 0 / 0 | 4.2e-14 / 4.2e-14 | 4.7e-13 / 4.0e-13 | 2.3e-13 / 3.4e-13 | 2400 / 2400 | 0 | equal |
| cusumkv_loops_clip | base / avx2 | 0 / 0 | 8.0e-14 / 8.0e-14 | 1.1e-12 / 7.1e-13 | 5.7e-13 / 6.4e-13 | 2400 / 2400 | 0 | equal |
| cusumkv_text_gap | base / avx2 | 0 / 0 | 4.2e-14 / 4.2e-14 | 2.3e-13 / 2.8e-13 | 2.3e-13 / 2.3e-13 | 2400 / 2400 | 0 | equal |

The x, z, s_m and s_v columns give the largest relative difference. The features are bit for bit:
the filters run the same operations in the same order as numpy. A planted bug, one output's k used
for all outputs, fails both cases of that node (`cusumkv_text_gap` and `cusumkv_confirm`) on both
paths. Their mean sums are off from the sixth sample on, by up to 100 %.

One finding in the Python reference: on an input gap the batch engine (`Node.alarms`) and the
streaming engine (`StreamNode`) differ. The batch filter bank (`FilterBank.run`, `_scan`) multiplies
whole blocks of 32 samples, so a NaN input also turns the filtered values before it in the same block
into NaN. The streaming engine sees NaN from the gap on. In `cusumk_input_gap` the two disagree on the
alarm. The native engine follows the streaming engine, which is the live one.

## Next steps

**N2, the engine: every node for each sample, the incidents, the graph rule** (`Engine.step`,
`Engine._name`, without the counterfactual). It also brings the source and the service:

- the plant service's `/info` and `/samples`, using the plant time of each row as the time stamp
  (rule 5);
- the document check (`documents_match`);
- the late start;
- `/healthz`, `/state`, `/incidents` and `/metrics`, with the state folder holding every node's state
  and the open incidents.

The test is `python -m live.engine check` turned into goldens: for each run, the alarms of every node
at every sample, the incidents (opened, candidates, victims, named, closed) and the graph root. N2
needs these from a real campaign:

- its `live-pack.pkl` and `models.pkl`, with the saved runs (the views). For example V30, whose 42
  runs the laptop check used, or the 660 scored runs on forge;
- the export of the real pack. This finds any node field N1 does not know. If the campaign used
  `--spike` (the eval-bench branch), the node kernel needs the spike rule first;
- the real sizes (25 nodes, the real inputs and outputs), for the time per sample on forge's CPU on
  both paths;
- runs long enough (8 h kept, the 24 h soak) to count the limit cases on real sums.

**N3, the twin and the root: the free and the reset replays over a window (`twin/twin.py`), and the
counterfactual (`eval/cf.py`: the trouble, the removed trouble, the target scores, the root).**
Replays are products of whole windows, which is where `matrixmultiply` comes in. It carries its own
run-time AVX and FMA kernels, and the base switch does not reach them, so `isa-scan` would fail.
Before N3, choose one of these:

- build it without `std` (SSE2 only), with the engine's own AVX2 product in `isa::avx2`;
- or let the scan allow its kernels, with a written exception to the switch.

N3 needs these from a real campaign:

- the pack's `sig2`;
- the calibration windows (`calib_job`);
- the fault runs and pairs, with `eval/cf.py`'s window terms, target scores and named roots as
  goldens;
- the same-root merges of the live engine on those runs.
