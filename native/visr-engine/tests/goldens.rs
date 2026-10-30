//! The native node against the Python engine (native/README.md, rule 1): the goldens of
//! native/tools/goldens.py, on the base path and on the AVX2 path.
//!
//!     python native/tools/goldens.py native/work/synthetic       (once; or VISR_GOLDENS=<folder>)
//!     cargo test --release -p visr-engine --test goldens -- --nocapture
//!
//! Done when z, the features and the CUSUM sums equal the Python values to a relative 1e-9 (relative to
//! the value, or to 1 below 1: z and the sums are in units of the residual spread), and the alarms are
//! equal sample for sample. A sum within rounding of its limit (or of 0) can move an alarm by a sample:
//! such a case is counted and reported, and every other difference fails the test. The node's state
//! after the middle sample equals Python's; the native node takes over Python's state and goes on with
//! the same numbers; its own state, saved and read back, goes on bit for bit.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use visr_engine::isa::{has_avx2_fma, Kernels, Path as IsaPath};
use visr_engine::neutral::Reader;
use visr_engine::{Node, NodePack, NodeState, Pack, Value};

const TOL: f64 = 1e-9;

fn root() -> PathBuf {
    match std::env::var("VISR_GOLDENS") {
        Ok(p) => PathBuf::from(p),
        Err(_) => Path::new(env!("CARGO_MANIFEST_DIR")).join("../work/synthetic"),
    }
}

/// |a - b| relative to the larger of |a|, |b| and 1. NaN against NaN is 0; NaN against a number is inf.
fn rel(a: f64, b: f64) -> f64 {
    match (a.is_nan(), b.is_nan()) {
        (true, true) => 0.0,
        (false, false) if a == b => 0.0,
        (false, false) => (a - b).abs() / a.abs().max(b.abs()).max(1.0),
        _ => f64::INFINITY,
    }
}

#[derive(Clone)]
struct Mat {
    cols: usize,
    v: Vec<f64>,
}

impl Mat {
    fn row(&self, t: usize) -> &[f64] {
        &self.v[t * self.cols..(t + 1) * self.cols]
    }
}

#[derive(Clone)]
struct Golden {
    case: String,
    node: String,
    lead: i64,
    state_after: usize,
    tags: Vec<String>,
    samples: Vec<Vec<Value>>,
    x: Mat,
    z: Mat,
    s_m: Mat,
    s_v: Mat,
    cover: Vec<bool>,
    on_m: Vec<bool>,
    on_v: Vec<bool>,
    alarm: Vec<bool>,
    cp: Vec<i64>,
    crossings: Vec<(i64, i64)>,
    dir: PathBuf,
}

fn load_golden(dir: &Path) -> Golden {
    let r = Reader::open(dir, "visr-goldens").unwrap_or_else(|e| panic!("{e}"));
    let m = r.manifest().clone();
    let n = m["samples"].as_u64().unwrap() as usize;
    let mut tags = Vec::new();
    let mut cols: Vec<Vec<Value>> = Vec::new();
    for t in m["tags"].as_array().unwrap() {
        tags.push(t["tag"].as_str().unwrap().to_string());
        let f = t["file"].as_str().unwrap();
        let col: Vec<Value> = if t["kind"] == "text" {
            let names: Vec<String> = t["names"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_string())
                .collect();
            r.vec_i64(f, Some(n))
                .unwrap()
                .iter()
                .map(|&c| {
                    if c < 0 {
                        Value::Missing
                    } else {
                        Value::Text(names[c as usize].clone())
                    }
                })
                .collect()
        } else {
            r.vec_f64(f, Some(n)).unwrap().into_iter().map(Value::Num).collect()
        };
        cols.push(col);
    }
    let samples = (0..n).map(|i| cols.iter().map(|c| c[i].clone()).collect()).collect();
    let e = &m["expect"];
    let mat = |k: &str| {
        let a = r.array(e[k].as_str().unwrap()).unwrap();
        Mat {
            cols: if a.shape.len() == 2 { a.shape[1] } else { 1 },
            v: a.to_f64(),
        }
    };
    let bools = |k: &str| r.vec_bool(e[k].as_str().unwrap(), None).unwrap();
    let cr = r.array(e["crossings"].as_str().unwrap()).unwrap().ints().unwrap();
    Golden {
        case: m["case"].as_str().unwrap().to_string(),
        node: m["node"].as_str().unwrap().to_string(),
        lead: m["lead"].as_i64().unwrap(),
        state_after: m["state_after"].as_u64().unwrap() as usize,
        tags,
        samples,
        x: mat("x"),
        z: mat("z"),
        s_m: mat("s_m"),
        s_v: mat("s_v"),
        cover: bools("cover"),
        on_m: bools("on_m"),
        on_v: bools("on_v"),
        alarm: bools("alarm"),
        cp: r.vec_i64(e["cp"].as_str().unwrap(), None).unwrap(),
        crossings: cr.chunks(2).map(|c| (c[0], c[1])).collect(),
        dir: dir.to_path_buf(),
    }
}

/// What the native node gives, per scored sample.
#[derive(Default)]
struct Got {
    t0: usize,
    x: Vec<Vec<f64>>,
    z: Vec<Vec<f64>>,
    s_m: Vec<Vec<f64>>,
    s_v: Vec<Vec<f64>>,
    cover: Vec<bool>,
    on_m: Vec<bool>,
    on_v: Vec<bool>,
    alarm: Vec<bool>,
    cp: Vec<i64>,
    crossings: Vec<(i64, i64)>,
    mid: Option<NodeState>,
}

/// Steps the samples from index `from`; scored sample `t0` is the first one recorded.
fn run(node: &mut Node, g: &Golden, from: usize, t0: usize, mid: Option<usize>) -> Got {
    let mut out = Got {
        t0,
        ..Default::default()
    };
    for s in &g.samples[from..] {
        let a = match node.step(s) {
            Some(a) => a,
            None => continue,
        };
        out.x.push(node.x().to_vec());
        out.z.push(node.z().unwrap().to_vec());
        out.s_m.push(node.s_m().to_vec());
        out.s_v
            .push(if node.pack().var() { node.s_v().to_vec() } else { vec![] });
        out.cover.push(node.cover());
        out.on_m.push(node.on_m());
        out.on_v.push(node.on_v());
        out.alarm.push(a);
        out.cp.push(node.change_point());
        out.crossings.extend_from_slice(node.crossings());
        if mid == Some(node.t() as usize) {
            out.mid = Some(node.state().clone());
        }
    }
    out
}

/// One difference at a limit: a sum within rounding of its limit or of 0, or a feature on the
/// coverage bound, that moves a latch, the coverage or an alarm.
#[derive(Debug)]
struct LimitCase {
    kind: &'static str,
    t: usize,
    detail: String,
}

#[derive(Default)]
struct Report {
    max_rel: [f64; 4], // x, z, s_m, s_v
    max_abs: [f64; 4],
    samples: usize,
    alarm_equal: usize,
    alarm_samples: usize,
    limit: Vec<LimitCase>,
    failures: Vec<String>,
}

fn coverage_bounds(nd: &NodePack, margin: f64) -> (Vec<f64>, Vec<f64>) {
    let m = &nd.model;
    let span: Vec<f64> = m.xhi.iter().zip(m.xlo.iter()).map(|(h, l)| h - l).collect();
    (
        m.xlo.iter().zip(&span).map(|(l, s)| l - margin * s).collect(),
        m.xhi.iter().zip(&span).map(|(h, s)| h + margin * s).collect(),
    )
}

/// Compares the scored samples t0.. of `got` with the goldens.
fn compare(g: &Golden, got: &Got, nd: &NodePack, margin: f64, confirm_n: usize) -> Report {
    let mut r = Report::default();
    let (lo, hi) = coverage_bounds(nd, margin);
    let near = |v: f64, lim: f64| (v - lim).abs() <= TOL * lim.abs().max(1.0);
    let mut flip_at: Vec<bool> = vec![false; got.alarm.len()];
    let mut latch_diff: Vec<bool> = vec![false; got.alarm.len()];
    for (i, t) in (got.t0..got.t0 + got.alarm.len()).enumerate() {
        r.samples += 1;
        let rows: [(&[f64], &[f64]); 4] = [
            (&got.x[i], g.x.row(t)),
            (&got.z[i], g.z.row(t)),
            (&got.s_m[i], g.s_m.row(t)),
            (&got.s_v[i], g.s_v.row(t)),
        ];
        for (q, (a, b)) in rows.iter().enumerate() {
            if a.len() != b.len() {
                r.failures
                    .push(format!("sample {t}: {} values, Python has {}", a.len(), b.len()));
                continue;
            }
            for (x, y) in a.iter().zip(b.iter()) {
                let e = rel(*x, *y);
                r.max_rel[q] = r.max_rel[q].max(e);
                if x.is_finite() && y.is_finite() {
                    r.max_abs[q] = r.max_abs[q].max((x - y).abs());
                }
                if e > TOL {
                    let what = ["x", "z", "s_m", "s_v"][q];
                    r.failures.push(format!(
                        "sample {t}: {what} {x:e} against Python {y:e} (relative {e:.2e})"
                    ));
                }
            }
        }
        // the comparisons against the limits and 0 that differ: each must be at the limit
        for (s, py, h, kind) in [
            (&got.s_m[i], g.s_m.row(t), nd.h, "mean sum"),
            (&got.s_v[i], g.s_v.row(t), nd.hv, "variance sum"),
        ] {
            for (j, (a, b)) in s.iter().zip(py.iter()).enumerate() {
                if (a > &h) != (b > &h) {
                    flip_at[i] = true;
                    if near(*b, h) && rel(*a, *b) <= TOL {
                        r.limit.push(LimitCase {
                            kind,
                            t,
                            detail: format!("sum {j} {a:e} against Python {b:e}, limit {h:e}"),
                        });
                    } else {
                        r.failures.push(format!(
                            "sample {t}: {kind} {j} on the other side of its limit, far from it"
                        ));
                    }
                }
                if (*a <= 0.0) != (*b <= 0.0) {
                    flip_at[i] = true;
                    if near(*b, 0.0) && rel(*a, *b) <= TOL {
                        r.limit.push(LimitCase {
                            kind,
                            t,
                            detail: format!("sum {j} {a:e} against Python {b:e} at 0"),
                        });
                    } else {
                        r.failures
                            .push(format!("sample {t}: {kind} {j} on the other side of 0, far from it"));
                    }
                }
            }
        }
        if got.cover[i] != g.cover[t] {
            flip_at[i] = true;
            let on_bound = got.x[i]
                .iter()
                .enumerate()
                .any(|(k, v)| near(*v, lo[k]) || near(*v, hi[k]));
            if on_bound {
                r.limit.push(LimitCase {
                    kind: "coverage",
                    t,
                    detail: "a feature on its coverage bound".into(),
                });
            } else {
                r.failures.push(format!(
                    "sample {t}: coverage {} against Python {}, no feature on a bound",
                    got.cover[i], g.cover[t]
                ));
            }
        }
        latch_diff[i] = got.on_m[i] != g.on_m[t] || got.on_v[i] != g.on_v[t] || got.cover[i] != g.cover[t];
    }
    // a latch that differs must have begun to differ at a limit case; an alarm that differs must
    // follow a latch that differs (within the confirmation)
    for i in 0..got.alarm.len() {
        let t = got.t0 + i;
        if latch_diff[i] && (i == 0 || !latch_diff[i - 1]) && !flip_at[i] {
            r.failures.push(format!(
                "sample {t}: a latch differs from Python with no sum at its limit"
            ));
        }
        if got.alarm[i] == g.alarm[t] {
            r.alarm_equal += 1;
        } else if !(i.saturating_sub(confirm_n)..=i).any(|k| latch_diff[k]) {
            r.failures.push(format!(
                "sample {t}: alarm {} against Python {}, no limit case before it",
                got.alarm[i], g.alarm[t]
            ));
        }
        r.alarm_samples += g.alarm[t] as usize;
        if got.cp[i] != g.cp[t] && !flip_at[..=i].iter().any(|&f| f) {
            r.failures.push(format!(
                "sample {t}: change point {} against Python {}",
                got.cp[i], g.cp[t]
            ));
        }
    }
    let want: Vec<(i64, i64)> = g
        .crossings
        .iter()
        .copied()
        .filter(|(t, _)| *t >= got.t0 as i64)
        .collect();
    if got.crossings != want && !flip_at.iter().any(|&f| f) {
        r.failures
            .push(format!("crossings {:?} against Python {:?}", got.crossings, want));
    }
    r
}

fn compare_state(a: &NodeState, b: &NodeState) -> Vec<String> {
    let mut bad = Vec::new();
    let mut f = |name: &str, x: &[f64], y: &[f64]| {
        if x.len() != y.len() || x.iter().zip(y).any(|(p, q)| rel(*p, *q) > TOL) {
            bad.push(name.to_string());
        }
    };
    f("c", &a.c, &b.c);
    f("mn", &a.mn, &b.mn);
    f("rs", &a.rs, &b.rs);
    f("rc", &a.rc, &b.rc);
    f("cv", &a.cv, &b.cv);
    f("mv", &a.mv, &b.mv);
    f("s_m", &a.s_m, &b.s_m);
    f("s_v", &a.s_v, &b.s_v);
    f("prev", &a.prev, &b.prev);
    f(
        "filters",
        a.filters.as_deref().unwrap_or(&[]),
        b.filters.as_deref().unwrap_or(&[]),
    );
    f("z", a.z.as_deref().unwrap_or(&[]), b.z.as_deref().unwrap_or(&[]));
    let ha: Vec<f64> = a
        .hist
        .iter()
        .flat_map(|(s, c)| s.iter().chain(c.iter()).copied())
        .collect();
    let hb: Vec<f64> = b
        .hist
        .iter()
        .flat_map(|(s, c)| s.iter().chain(c.iter()).copied())
        .collect();
    f("hist", &ha, &hb);
    let exact = [
        ("lead", a.lead == b.lead),
        ("taken", a.taken == b.taken),
        ("t", a.t == b.t),
        ("on_m", a.on_m == b.on_m),
        ("on_v", a.on_v == b.on_v),
        ("run_len", a.run_len == b.run_len),
        ("prev_bin", a.prev_bin == b.prev_bin),
        ("last_change", a.last_change == b.last_change),
        ("lz", a.lz_m == b.lz_m && a.lz_v == b.lz_v),
        ("up", a.up_m == b.up_m && a.up_v == b.up_v),
        ("above", a.above_m == b.above_m && a.above_v == b.above_v),
        ("alarm", a.alarm == b.alarm),
        ("cover", a.cover == b.cover),
        ("prev_set", a.prev_set == b.prev_set),
        ("hist length", a.hist.len() == b.hist.len()),
    ];
    bad.extend(exact.iter().filter(|(_, ok)| !ok).map(|(n, _)| n.to_string()));
    bad
}

fn bits_equal(a: &Got, b: &Got) -> bool {
    let fb = |x: &Vec<Vec<f64>>, y: &Vec<Vec<f64>>| {
        x.len() == y.len()
            && x.iter()
                .zip(y)
                .all(|(p, q)| p.len() == q.len() && p.iter().zip(q).all(|(u, v)| u.to_bits() == v.to_bits()))
    };
    fb(&a.x, &b.x)
        && fb(&a.z, &b.z)
        && fb(&a.s_m, &b.s_m)
        && fb(&a.s_v, &b.s_v)
        && a.alarm == b.alarm
        && a.cover == b.cover
        && a.cp == b.cp
        && a.crossings == b.crossings
}

#[test]
fn native_node_equals_python() {
    let root = root();
    let pack_dir = root.join("pack");
    assert!(
        pack_dir.join("manifest.json").exists(),
        "no goldens at {}: run python native/tools/goldens.py native/work/synthetic (or set VISR_GOLDENS)",
        root.display()
    );
    let pack = Pack::load(&pack_dir).unwrap_or_else(|e| panic!("{e}"));
    let mut cases: Vec<PathBuf> = std::fs::read_dir(root.join("goldens"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    cases.sort();
    let mut paths = vec![Kernels::base()];
    if has_avx2_fma() {
        paths.push(Kernels::new(IsaPath::Avx2Fma).unwrap());
    } else {
        println!("this processor lacks AVX2 or FMA: the base path only");
    }
    let tmp = std::env::temp_dir().join(format!("visr-goldens-{}", std::process::id()));
    let mut failures = Vec::new();
    let mut limit_total = 0;
    println!(
        "{:<22} {:<8} {:>6} {:>9} {:>9} {:>9} {:>9} {:>11} {:>6} {:>5} {:>8} {:>8}",
        "case",
        "path",
        "scored",
        "x rel",
        "z rel",
        "s_m rel",
        "s_v rel",
        "alarms =",
        "alarm",
        "limit",
        "py state",
        "takeover"
    );
    for dir in &cases {
        let g = load_golden(dir);
        let nd: Arc<NodePack> = pack
            .node(&g.node)
            .unwrap_or_else(|| panic!("no node {} in the pack", g.node))
            .clone();
        let confirm_n = if nd.confirm_s != 0.0 {
            (nd.confirm_s / pack.constants.sample_s) as usize
        } else {
            0
        };
        let margin = pack.constants.coverage_margin;
        for &k in &paths {
            let mut node = Node::new(nd.clone(), &pack.constants, k);
            node.bind(&g.tags);
            node.reset(g.lead);
            let full = run(&mut node, &g, 0, 0, Some(g.state_after));
            let rep = compare(&g, &full, &nd, margin, confirm_n);
            // the state after the middle sample, against Python's
            let py_state = NodeState::load(g.dir.join("state"), &nd, &pack.manifest_sha256, node.hist_max())
                .unwrap_or_else(|e| panic!("{e}"));
            let state_bad = compare_state(full.mid.as_ref().unwrap(), &py_state);
            // the takeover: Python's state, then the rest of the run
            let from = g.lead as usize + g.state_after + 1;
            let mut taker = Node::new(nd.clone(), &pack.constants, k);
            taker.bind(&g.tags);
            taker.set_state(py_state).unwrap();
            let rest = run(&mut taker, &g, from, g.state_after + 1, None);
            let rep2 = compare(&g, &rest, &nd, margin, confirm_n);
            // the native state saved and read back: the same bits as the run that never stopped
            let sdir = tmp.join(format!("{}-{}", g.case, k.path().name()));
            full.mid
                .as_ref()
                .unwrap()
                .save(&sdir, &nd, &pack.manifest_sha256)
                .unwrap();
            let back = NodeState::load(&sdir, &nd, &pack.manifest_sha256, node.hist_max()).unwrap();
            assert!(
                back.same_bits(full.mid.as_ref().unwrap()),
                "{}: the saved state reads back the same bits",
                g.case
            );
            let mut again = Node::new(nd.clone(), &pack.constants, k);
            again.bind(&g.tags);
            again.set_state(back).unwrap();
            let cont = run(&mut again, &g, from, g.state_after + 1, None);
            let tail = Got {
                t0: g.state_after + 1,
                x: full.x[g.state_after + 1..].to_vec(),
                z: full.z[g.state_after + 1..].to_vec(),
                s_m: full.s_m[g.state_after + 1..].to_vec(),
                s_v: full.s_v[g.state_after + 1..].to_vec(),
                cover: full.cover[g.state_after + 1..].to_vec(),
                on_m: vec![],
                on_v: vec![],
                alarm: full.alarm[g.state_after + 1..].to_vec(),
                cp: full.cp[g.state_after + 1..].to_vec(),
                crossings: full
                    .crossings
                    .iter()
                    .copied()
                    .filter(|(t, _)| *t > g.state_after as i64)
                    .collect(),
                mid: None,
            };
            let round_trip = bits_equal(&cont, &tail);
            println!(
                "{:<22} {:<8} {:>6} {:>9.1e} {:>9.1e} {:>9.1e} {:>9.1e} {:>5}/{:<5} {:>6} {:>5} {:>8} {:>8}",
                g.case,
                k.path().name(),
                rep.samples,
                rep.max_rel[0],
                rep.max_rel[1],
                rep.max_rel[2],
                rep.max_rel[3],
                rep.alarm_equal,
                rep.samples,
                rep.alarm_samples,
                rep.limit.len() + rep2.limit.len(),
                if state_bad.is_empty() { "equal" } else { "DIFFERS" },
                if rep2.failures.is_empty() && round_trip {
                    "equal"
                } else {
                    "DIFFERS"
                },
            );
            for c in rep.limit.iter().chain(&rep2.limit) {
                println!("    limit case: {} at sample {}: {}", c.kind, c.t, c.detail);
            }
            limit_total += rep.limit.len() + rep2.limit.len();
            let tag = format!("{} ({})", g.case, k.path().name());
            failures.extend(rep.failures.iter().take(5).map(|f| format!("{tag}: {f}")));
            failures.extend(
                rep2.failures
                    .iter()
                    .take(5)
                    .map(|f| format!("{tag} after the takeover: {f}")),
            );
            if !state_bad.is_empty() {
                failures.push(format!(
                    "{tag}: the state after sample {} differs from Python's in {state_bad:?}",
                    g.state_after
                ));
            }
            if !round_trip {
                failures.push(format!(
                    "{tag}: the saved and restored state does not go on bit for bit"
                ));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&tmp);
    println!("limit cases: {limit_total}");
    assert!(
        !cases.is_empty(),
        "no golden cases under {}",
        root.join("goldens").display()
    );
    assert!(
        failures.is_empty(),
        "{} differences:\n{}",
        failures.len(),
        failures.join("\n")
    );
}

/// The golden itself as the native result (every value equal).
fn as_got(g: &Golden) -> Got {
    let n = g.alarm.len();
    let rows = |m: &Mat| (0..n).map(|t| m.row(t).to_vec()).collect::<Vec<_>>();
    Got {
        t0: 0,
        x: rows(&g.x),
        z: rows(&g.z),
        s_m: rows(&g.s_m),
        s_v: rows(&g.s_v),
        cover: g.cover.clone(),
        on_m: g.on_m.clone(),
        on_v: g.on_v.clone(),
        alarm: g.alarm.clone(),
        cp: g.cp.clone(),
        crossings: g.crossings.clone(),
        mid: None,
    }
}

#[test]
fn a_sum_at_its_limit_is_counted_not_failed() {
    let root = root();
    let pack = Pack::load(root.join("pack")).unwrap_or_else(|e| panic!("{e}"));
    let g = load_golden(&root.join("goldens/cusum_plain"));
    let nd = pack.node(&g.node).unwrap().clone();
    // the first alarm: Python's largest sum passes h there
    let t = (1..g.alarm.len())
        .find(|&t| g.alarm[t] && !g.alarm[t - 1])
        .expect("an alarm in the case");
    let j = (0..g.s_m.cols)
        .max_by(|&a, &b| g.s_m.row(t)[a].total_cmp(&g.s_m.row(t)[b]))
        .unwrap();
    // Python a hair above the limit, the native node a hair below: its alarm comes one sample later
    let mut py = g.clone();
    py.s_m.v[t * py.s_m.cols + j] = nd.h * (1.0 + 1e-13);
    let mut got = as_got(&py);
    got.s_m[t][j] = nd.h * (1.0 - 1e-13);
    got.on_m[t] = false;
    got.alarm[t] = false;
    got.crossings.retain(|c| c.0 != t as i64);
    let r = compare(&py, &got, &nd, pack.constants.coverage_margin, 0);
    assert!(r.failures.is_empty(), "{:?}", r.failures);
    assert_eq!(r.limit.len(), 1, "{:?}", r.limit);
    assert_eq!((r.limit[0].kind, r.limit[0].t), ("mean sum", t));
    // the same alarm one sample late with the sum far from its limit is a failure
    got.s_m[t][j] = nd.h * 0.999;
    let r = compare(&py, &got, &nd, pack.constants.coverage_margin, 0);
    assert!(!r.failures.is_empty() && r.limit.is_empty());
}
