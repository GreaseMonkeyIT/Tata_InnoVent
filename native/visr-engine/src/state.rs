//! The state of one node between two samples, and its portable form (native/README.md, "The node
//! state"): the filters, the CUSUM sums, the window of the rolling mean, the latches and the change
//! point trackers. The form is the neutral folder of the pack (manifest.json and .npy files with their
//! SHA-256), with the same names as live/engine.py StreamNode, so a state can pass between the Python
//! engine and this one, and from one PC to another at a takeover.

use std::collections::VecDeque;
use std::path::Path;

use serde_json::{json, Value};

use crate::err;
use crate::error::{Context, Result};
use crate::neutral::{Reader, Writer};
use crate::npy::Data;
use crate::pack::NodePack;

pub const FORMAT: &str = "visr-node-state";
pub const VERSION: u64 = 1;

#[derive(Debug, Clone, PartialEq)]
pub struct NodeState {
    /// samples at the start of the run that only warm the filters
    pub lead: i64,
    /// samples taken since the reset
    pub taken: i64,
    /// the index of the last scored sample (-1 before the first)
    pub t: i64,
    /// the filters, n_in x n_tau row-major (None before the first sample)
    pub filters: Option<Vec<f64>>,
    /// the cumulative sums of the mean CUSUM (both sides: the n outputs up, then the n down) and
    /// their running minimum
    pub c: Vec<f64>,
    pub mn: Vec<f64>,
    /// the rolling mean's cumulative sums and counts, and their last var_win + 1 values
    pub rs: Vec<f64>,
    pub rc: Vec<f64>,
    pub hist: VecDeque<(Vec<f64>, Vec<f64>)>,
    /// the variance CUSUM's cumulative sum and running minimum
    pub cv: Vec<f64>,
    pub mv: Vec<f64>,
    pub on_m: bool,
    pub on_v: bool,
    /// samples in a row with the alarm on (the confirmation)
    pub run_len: i64,
    /// the PLC state of the last sample, and the sample of its last change (the steady mask)
    pub prev_bin: Option<i64>,
    pub last_change: Option<i64>,
    /// per sum: the last sample at or below 0, whether it passed its limit since, whether it is above
    pub lz_m: Vec<i64>,
    pub lz_v: Vec<i64>,
    pub up_m: Vec<bool>,
    pub up_v: Vec<bool>,
    pub above_m: Vec<bool>,
    pub above_v: Vec<bool>,
    /// the sums: s_m = c - mn (2n), s_v = cv - mv (n)
    pub s_m: Vec<f64>,
    pub s_v: Vec<f64>,
    pub alarm: bool,
    pub cover: bool,
    pub z: Option<Vec<f64>>,
    /// the last value of each change input, and whether there is one yet
    pub prev: Vec<f64>,
    pub prev_set: Vec<bool>,
}

impl NodeState {
    /// The state of live/engine.py StreamNode.reset(lead).
    pub fn new(n_out: usize, n_delta: usize, lead: i64) -> NodeState {
        let n = n_out;
        NodeState {
            lead,
            taken: 0,
            t: -1,
            filters: None,
            c: vec![0.0; 2 * n],
            mn: vec![0.0; 2 * n],
            rs: vec![0.0; n],
            rc: vec![0.0; n],
            hist: VecDeque::from([(vec![0.0; n], vec![0.0; n])]),
            cv: vec![0.0; n],
            mv: vec![0.0; n],
            on_m: false,
            on_v: false,
            run_len: 0,
            prev_bin: None,
            last_change: None,
            lz_m: vec![-1; 2 * n],
            lz_v: vec![-1; n],
            up_m: vec![false; 2 * n],
            up_v: vec![false; n],
            above_m: vec![false; 2 * n],
            above_v: vec![false; n],
            s_m: vec![0.0; 2 * n],
            s_v: vec![0.0; n],
            alarm: false,
            cover: false,
            z: None,
            prev: vec![f64::NAN; n_delta],
            prev_set: vec![false; n_delta],
        }
    }

    /// The same state, bit for bit (NaN included, which `==` never finds equal).
    pub fn same_bits(&self, o: &NodeState) -> bool {
        fn f(a: &[f64], b: &[f64]) -> bool {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
        }
        fn of(a: &Option<Vec<f64>>, b: &Option<Vec<f64>>) -> bool {
            match (a, b) {
                (Some(a), Some(b)) => f(a, b),
                (None, None) => true,
                _ => false,
            }
        }
        (
            self.lead,
            self.taken,
            self.t,
            self.on_m,
            self.on_v,
            self.run_len,
            self.prev_bin,
            self.last_change,
        ) == (
            o.lead,
            o.taken,
            o.t,
            o.on_m,
            o.on_v,
            o.run_len,
            o.prev_bin,
            o.last_change,
        ) && (self.alarm, self.cover) == (o.alarm, o.cover)
            && (
                &self.lz_m,
                &self.lz_v,
                &self.up_m,
                &self.up_v,
                &self.above_m,
                &self.above_v,
                &self.prev_set,
            ) == (&o.lz_m, &o.lz_v, &o.up_m, &o.up_v, &o.above_m, &o.above_v, &o.prev_set)
            && of(&self.filters, &o.filters)
            && of(&self.z, &o.z)
            && [
                (&self.c, &o.c),
                (&self.mn, &o.mn),
                (&self.rs, &o.rs),
                (&self.rc, &o.rc),
                (&self.cv, &o.cv),
            ]
            .iter()
            .all(|(a, b)| f(a, b))
            && [
                (&self.mv, &o.mv),
                (&self.s_m, &o.s_m),
                (&self.s_v, &o.s_v),
                (&self.prev, &o.prev),
            ]
            .iter()
            .all(|(a, b)| f(a, b))
            && self.hist.len() == o.hist.len()
            && self.hist.iter().zip(&o.hist).all(|((a, b), (c, d))| f(a, c) && f(b, d))
    }

    /// Checks the sizes against the node (a state of another node, or of another pack, is refused
    /// before the folder is read).
    pub fn check(&self, nd: &NodePack, hist_max: usize) -> Result<()> {
        let n = nd.n_out();
        let n_f = nd.model.n_in() * nd.model.taus.len();
        let sizes = [
            ("c", self.c.len(), 2 * n),
            ("mn", self.mn.len(), 2 * n),
            ("rs", self.rs.len(), n),
            ("rc", self.rc.len(), n),
            ("cv", self.cv.len(), n),
            ("mv", self.mv.len(), n),
            ("lz_m", self.lz_m.len(), 2 * n),
            ("lz_v", self.lz_v.len(), n),
            ("up_m", self.up_m.len(), 2 * n),
            ("up_v", self.up_v.len(), n),
            ("above_m", self.above_m.len(), 2 * n),
            ("above_v", self.above_v.len(), n),
            ("s_m", self.s_m.len(), 2 * n),
            ("s_v", self.s_v.len(), n),
            ("prev", self.prev.len(), nd.delta_in.len()),
            ("prev_set", self.prev_set.len(), nd.delta_in.len()),
        ];
        for (name, got, want) in sizes {
            if got != want {
                return err!("state of node {}: {name} has {got} values, {want} expected", nd.name);
            }
        }
        if self.filters.as_ref().is_some_and(|f| f.len() != n_f) {
            return err!(
                "state of node {}: {} filter values, {n_f} expected",
                nd.name,
                self.filters.as_ref().unwrap().len()
            );
        }
        if self.z.as_ref().is_some_and(|z| z.len() != n) {
            return err!(
                "state of node {}: z of {} values, {n} expected",
                nd.name,
                self.z.as_ref().unwrap().len()
            );
        }
        if self.hist.is_empty()
            || self.hist.len() > hist_max
            || self.hist.iter().any(|(s, c)| s.len() != n || c.len() != n)
        {
            return err!(
                "state of node {}: a rolling window of {} entries, 1 to {hist_max} of {n} values expected",
                nd.name,
                self.hist.len()
            );
        }
        if self.taken < 0 || self.lead < 0 || self.t < -1 {
            return err!("state of node {}: counts out of range", nd.name);
        }
        Ok(())
    }

    /// Writes the portable form to `dir` (its manifest last). Returns the manifest's SHA-256.
    pub fn save(&self, dir: impl AsRef<Path>, node: &NodePack, pack_sha256: &str) -> Result<String> {
        let n = node.n_out();
        let mut w = Writer::create(dir.as_ref())?;
        let f = |v: &[f64]| Data::F64(v.to_vec());
        let b = |v: &[bool]| Data::Bool(v.to_vec());
        let l = self.hist.len();
        let mut arrays = serde_json::Map::new();
        let mut put = |w: &mut Writer, name: &str, shape: &[usize], d: Data| -> Result<()> {
            let rel = w.array(&format!("{name}.npy"), shape, d)?;
            arrays.insert(name.to_string(), Value::String(rel));
            Ok(())
        };
        put(&mut w, "c", &[2 * n], f(&self.c))?;
        put(&mut w, "mn", &[2 * n], f(&self.mn))?;
        put(&mut w, "rs", &[n], f(&self.rs))?;
        put(&mut w, "rc", &[n], f(&self.rc))?;
        put(
            &mut w,
            "hist_rs",
            &[l, n],
            Data::F64(self.hist.iter().flat_map(|h| h.0.iter().copied()).collect()),
        )?;
        put(
            &mut w,
            "hist_rc",
            &[l, n],
            Data::F64(self.hist.iter().flat_map(|h| h.1.iter().copied()).collect()),
        )?;
        put(&mut w, "cv", &[n], f(&self.cv))?;
        put(&mut w, "mv", &[n], f(&self.mv))?;
        put(&mut w, "s_m", &[2 * n], f(&self.s_m))?;
        put(&mut w, "s_v", &[n], f(&self.s_v))?;
        put(&mut w, "lz_m", &[2 * n], Data::I64(self.lz_m.clone()))?;
        put(&mut w, "lz_v", &[n], Data::I64(self.lz_v.clone()))?;
        put(&mut w, "up_m", &[2 * n], b(&self.up_m))?;
        put(&mut w, "up_v", &[n], b(&self.up_v))?;
        put(&mut w, "above_m", &[2 * n], b(&self.above_m))?;
        put(&mut w, "above_v", &[n], b(&self.above_v))?;
        put(&mut w, "prev", &[self.prev.len()], f(&self.prev))?;
        put(&mut w, "prev_set", &[self.prev_set.len()], b(&self.prev_set))?;
        if let Some(fl) = &self.filters {
            put(&mut w, "filters", &[node.model.n_in(), node.model.taus.len()], f(fl))?;
        }
        if let Some(z) = &self.z {
            put(&mut w, "z", &[n], f(z))?;
        }
        w.finish(json!({
            "format": FORMAT,
            "version": VERSION,
            "node": node.name,
            "pack_manifest_sha256": pack_sha256,
            "written_by": concat!("visr-engine ", env!("CARGO_PKG_VERSION")),
            "scalars": {
                "lead": self.lead, "taken": self.taken, "t": self.t, "on_m": self.on_m, "on_v": self.on_v,
                "run_len": self.run_len, "prev_bin": self.prev_bin, "last_change": self.last_change,
                "alarm": self.alarm, "cover": self.cover,
            },
            "arrays": arrays,
        }))
    }

    /// Reads a portable state of `node` from the pack with manifest hash `pack_sha256`.
    pub fn load(dir: impl AsRef<Path>, node: &NodePack, pack_sha256: &str, hist_max: usize) -> Result<NodeState> {
        let r = Reader::open(dir.as_ref(), FORMAT)?;
        let m = r.manifest();
        let what = || format!("node state {}", dir.as_ref().display());
        if m["version"].as_u64() != Some(VERSION) {
            return err!("{}: version {}, {VERSION} expected", what(), m["version"]);
        }
        if m["node"].as_str() != Some(node.name.as_str()) {
            return err!("{}: the state of node {}, not of {}", what(), m["node"], node.name);
        }
        if m["pack_manifest_sha256"].as_str() != Some(pack_sha256) {
            return err!("{}: the state of another pack ({})", what(), m["pack_manifest_sha256"]);
        }
        let s = &m["scalars"];
        let int = |k: &str| {
            s[k].as_i64()
                .ok_or_else(|| crate::error::Error(format!("{}: {k} is not an integer", what())))
        };
        let flag = |k: &str| {
            s[k].as_bool()
                .ok_or_else(|| crate::error::Error(format!("{}: {k} is not true or false", what())))
        };
        let opt = |k: &str| -> Result<Option<i64>> {
            match &s[k] {
                Value::Null => Ok(None),
                v => v
                    .as_i64()
                    .map(Some)
                    .ok_or_else(|| crate::error::Error(format!("{}: {k}", what()))),
            }
        };
        let a = &m["arrays"];
        let file = |k: &str| -> Result<String> {
            a[k].as_str()
                .map(str::to_string)
                .ok_or_else(|| crate::error::Error(format!("{}: no array {k}", what())))
        };
        let fv = |k: &str| r.vec_f64(&file(k)?, None);
        let bv = |k: &str| r.vec_bool(&file(k)?, None);
        let iv = |k: &str| r.vec_i64(&file(k)?, None);
        let (_, n_rs, hrs) = r.mat_f64(&file("hist_rs")?, None, None)?;
        let (_, n_rc, hrc) = r.mat_f64(&file("hist_rc")?, None, None)?;
        if n_rs != node.n_out() || n_rc != node.n_out() || hrs.len() != hrc.len() {
            return err!("{}: the rolling window does not fit node {}", what(), node.name);
        }
        let hist = hrs
            .chunks(n_rs.max(1))
            .zip(hrc.chunks(n_rc.max(1)))
            .map(|(a, b)| (a.to_vec(), b.to_vec()))
            .collect();
        let st = NodeState {
            lead: int("lead")?,
            taken: int("taken")?,
            t: int("t")?,
            filters: if a.get("filters").is_some() {
                Some(
                    r.mat_f64(&file("filters")?, Some(node.model.n_in()), Some(node.model.taus.len()))?
                        .2,
                )
            } else {
                None
            },
            c: fv("c")?,
            mn: fv("mn")?,
            rs: fv("rs")?,
            rc: fv("rc")?,
            hist,
            cv: fv("cv")?,
            mv: fv("mv")?,
            on_m: flag("on_m")?,
            on_v: flag("on_v")?,
            run_len: int("run_len")?,
            prev_bin: opt("prev_bin")?,
            last_change: opt("last_change")?,
            lz_m: iv("lz_m")?,
            lz_v: iv("lz_v")?,
            up_m: bv("up_m")?,
            up_v: bv("up_v")?,
            above_m: bv("above_m")?,
            above_v: bv("above_v")?,
            s_m: fv("s_m")?,
            s_v: fv("s_v")?,
            alarm: flag("alarm")?,
            cover: flag("cover")?,
            z: if a.get("z").is_some() { Some(fv("z")?) } else { None },
            prev: fv("prev")?,
            prev_set: bv("prev_set")?,
        };
        st.check(node, hist_max).ctx(what)?;
        Ok(st)
    }
}
