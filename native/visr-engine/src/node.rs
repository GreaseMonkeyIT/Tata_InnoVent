//! One node, one sample at a time: live/engine.py StreamNode.step, operation for operation (the
//! reference, native/README.md rule 1). The filters, the network, the coverage test, z, the CUSUM on
//! the mean and on the variance, the confirmation, the crossings and the change point. Every constant
//! comes from the pack.

// The index loops follow the vector arithmetic of the Python code, over several arrays at once.
#![allow(clippy::needless_range_loop)]

use std::collections::HashMap;
use std::sync::Arc;

use crate::error::Result;
use crate::isa::Kernels;
use crate::pack::{Constants, NodePack};
use crate::sample::{Value, MISSING};
use crate::state::NodeState;

/// Where the node's tags are in a sample (the order of the source's tags).
#[derive(Debug, Clone)]
struct Binding {
    num_in: Vec<Option<usize>>,
    cats: Vec<Option<usize>>,
    delta_in: Vec<Option<usize>>,
    measures: Vec<Option<usize>>,
    /// the index of the state command among the text inputs
    state_cat: Option<usize>,
    /// for each measure, the index of its categories among out_cats (a text output)
    out_cat: Vec<Option<usize>>,
}

pub struct Node {
    nd: Arc<NodePack>,
    kern: Kernels,
    /// exp(-sample_s / tau) and 1 - that, per filter
    a: Vec<f64>,
    om: Vec<f64>,
    /// the coverage bounds: the training range widened by the margin on each side
    lo: Vec<f64>,
    hi: Vec<f64>,
    /// the feature columns that count for coverage
    cov_idx: Vec<usize>,
    /// samples in the rolling mean's window, and half of it
    win: usize,
    half_win: f64,
    confirm_n: Option<i64>,
    steady_n: Option<i64>,
    bind: Option<Binding>,
    st: NodeState,
    crossings: Vec<(i64, i64)>,
    // the vectors of one step, kept to save allocations; x is the last scored sample's features
    u: Vec<f64>,
    yv: Vec<f64>,
    x: Vec<f64>,
    xn: Vec<f64>,
    h0: Vec<f64>,
    h1: Vec<f64>,
    lin: Vec<f64>,
}

impl Node {
    pub fn new(nd: Arc<NodePack>, c: &Constants, kern: Kernels) -> Node {
        let m = &nd.model;
        let a: Vec<f64> = m.taus.iter().map(|tau| (-c.sample_s / tau).exp()).collect();
        let om = a.iter().map(|a| 1.0 - a).collect();
        let span: Vec<f64> = m.xhi.iter().zip(m.xlo.iter()).map(|(h, l)| h - l).collect();
        let lo = m
            .xlo
            .iter()
            .zip(&span)
            .map(|(l, s)| l - c.coverage_margin * s)
            .collect();
        let hi = m
            .xhi
            .iter()
            .zip(&span)
            .map(|(h, s)| h + c.coverage_margin * s)
            .collect();
        let cov_idx = match &m.cov_cols {
            None => (0..m.n_x()).collect(),
            Some(cc) => cc.iter().enumerate().filter(|(_, &k)| k).map(|(i, _)| i).collect(),
        };
        let win = (c.var_win_s / c.sample_s) as usize;
        let confirm_n = (nd.confirm_s != 0.0).then(|| (nd.confirm_s / c.sample_s) as i64);
        let steady_n = (nd.var_steady != 0.0 && nd.state_cmd.is_some()).then(|| (nd.var_steady / c.sample_s) as i64);
        let st = NodeState::new(nd.n_out(), nd.delta_in.len(), 0);
        Node {
            kern,
            a,
            om,
            lo,
            hi,
            cov_idx,
            win,
            half_win: win as f64 / 2.0,
            confirm_n,
            steady_n,
            bind: None,
            st,
            crossings: Vec::new(),
            u: Vec::new(),
            yv: Vec::new(),
            x: Vec::new(),
            xn: Vec::new(),
            h0: Vec::new(),
            h1: Vec::new(),
            lin: Vec::new(),
            nd,
        }
    }

    pub fn pack(&self) -> &Arc<NodePack> {
        &self.nd
    }

    pub fn kernels(&self) -> Kernels {
        self.kern
    }

    /// The order of the tags in the samples to come. A tag the source does not have reads as missing.
    pub fn bind(&mut self, tags: &[String]) {
        let ix: HashMap<&str, usize> = tags.iter().enumerate().map(|(i, t)| (t.as_str(), i)).collect();
        let at = |t: &String| ix.get(t.as_str()).copied();
        let nd = &self.nd;
        self.bind = Some(Binding {
            num_in: nd.num_in.iter().map(at).collect(),
            cats: nd.cats.iter().map(|c| at(&c.tag)).collect(),
            delta_in: nd.delta_in.iter().map(at).collect(),
            measures: nd.measures.iter().map(at).collect(),
            state_cat: nd
                .state_cmd
                .as_ref()
                .and_then(|s| nd.cats.iter().position(|c| &c.tag == s)),
            out_cat: nd
                .measures
                .iter()
                .map(|t| nd.out_cats.iter().position(|c| &c.tag == t))
                .collect(),
        });
    }

    /// A new run: `lead` samples only warm the filters, as in a batch run.
    pub fn reset(&mut self, lead: i64) {
        self.st = NodeState::new(self.nd.n_out(), self.nd.delta_in.len(), lead);
        self.crossings.clear();
    }

    pub fn state(&self) -> &NodeState {
        &self.st
    }

    /// Takes over a state (of this node, from this pack: NodeState::load checks).
    pub fn set_state(&mut self, st: NodeState) -> Result<()> {
        st.check(&self.nd, self.win + 1)?;
        self.st = st;
        self.crossings.clear();
        Ok(())
    }

    pub fn hist_max(&self) -> usize {
        self.win + 1
    }

    /// The last scored sample's index, z, sums, coverage, latches and alarm.
    pub fn t(&self) -> i64 {
        self.st.t
    }
    pub fn z(&self) -> Option<&[f64]> {
        self.st.z.as_deref()
    }
    pub fn s_m(&self) -> &[f64] {
        &self.st.s_m
    }
    pub fn s_v(&self) -> &[f64] {
        &self.st.s_v
    }
    pub fn cover(&self) -> bool {
        self.st.cover
    }
    pub fn on_m(&self) -> bool {
        self.st.on_m
    }
    pub fn on_v(&self) -> bool {
        self.st.on_v
    }
    pub fn alarm(&self) -> bool {
        self.st.alarm
    }
    /// The features of the last scored sample: the inputs, then each input's filters.
    pub fn x(&self) -> &[f64] {
        &self.x
    }
    /// The sums that crossed their limit at the last sample: (sample, change point).
    pub fn crossings(&self) -> &[(i64, i64)] {
        &self.crossings
    }

    /// The input and output vectors of a sample (StreamNode._uy). It moves the change inputs on.
    fn uy(&mut self, s: &[Value]) {
        let b = self.bind.as_ref().expect("Node::bind before Node::step");
        let nd = &*self.nd;
        let get = |i: Option<usize>| -> &Value { i.and_then(|i| s.get(i)).unwrap_or(&MISSING) };
        self.u.clear();
        for &i in &b.num_in {
            self.u.push(get(i).num());
        }
        for (c, &i) in nd.cats.iter().zip(&b.cats) {
            let v = get(i);
            for cat in &c.values {
                self.u.push(if v.is(cat) { 1.0 } else { 0.0 });
            }
        }
        for (j, &i) in b.delta_in.iter().enumerate() {
            let v = get(i).num();
            let p = if self.st.prev_set[j] { self.st.prev[j] } else { v };
            // a step down is a reset: the change is then the new value
            self.u.push(if nd.counter_resets && v < p { v } else { v - p });
            self.st.prev[j] = v;
            self.st.prev_set[j] = true;
        }
        self.yv.clear();
        for (&i, oc) in b.measures.iter().zip(&b.out_cat) {
            let v = get(i);
            match oc {
                Some(k) => {
                    for cat in &nd.out_cats[*k].values {
                        self.yv.push(if v.is(cat) { 1.0 } else { 0.0 });
                    }
                }
                None => self.yv.push(v.num()),
            }
        }
    }

    /// The model's prediction of the outputs from the features in self.x, into self.h1.
    fn predict(&mut self) {
        let m = &self.nd.model;
        self.xn.clear();
        self.xn.extend(
            self.x
                .iter()
                .zip(m.xm.iter())
                .zip(m.xs.iter())
                .map(|((x, a), s)| (x - a) / s),
        );
        let last = m.mlp.len() - 1;
        self.h0.clear();
        self.h0.extend_from_slice(&self.xn);
        for (l, layer) in m.mlp.iter().enumerate() {
            self.h1.resize(layer.n_out(), 0.0);
            self.kern
                .dense(&self.h0, layer.w.as_slice().expect("standard layout"), &mut self.h1);
            for (o, b) in self.h1.iter_mut().zip(layer.b.iter()) {
                *o += b;
            }
            if l < last {
                self.h1.iter_mut().for_each(|v| *v = v.tanh());
                std::mem::swap(&mut self.h0, &mut self.h1);
            }
        }
        // the linear path on the same standardized features: (MLP + xn L) + l, then scaled back
        self.lin.resize(m.linear.n_out(), 0.0);
        self.kern
            .dense(&self.xn, m.linear.w.as_slice().expect("standard layout"), &mut self.lin);
        for (j, o) in self.h1.iter_mut().enumerate() {
            *o = ((*o + self.lin[j]) + m.linear.b[j]) * m.ys[j] + m.ym[j];
        }
    }

    fn covered(&self) -> bool {
        self.cov_idx
            .iter()
            .all(|&i| self.x[i] >= self.lo[i] && self.x[i] <= self.hi[i])
    }

    /// Takes one sample, in the order of `bind`. Returns the alarm, None during the lead.
    pub fn step(&mut self, s: &[Value]) -> Option<bool> {
        self.uy(s);
        let n_in = self.u.len();
        let nt = self.a.len();
        let f = self
            .st
            .filters
            .get_or_insert_with(|| self.u.iter().flat_map(|&u| std::iter::repeat_n(u, nt)).collect());
        for i in 0..n_in {
            for j in 0..nt {
                let k = i * nt + j;
                f[k] = self.a[j] * f[k] + self.om[j] * self.u[i];
            }
        }
        self.st.taken += 1;
        if self.st.taken <= self.st.lead {
            return None;
        }
        self.st.t += 1;
        self.crossings.clear();
        self.x.clear();
        self.x.extend_from_slice(&self.u);
        self.x.extend_from_slice(self.st.filters.as_ref().unwrap());
        self.predict();
        let cov = self.covered();
        let nd = Arc::clone(&self.nd);
        let n = nd.n_out();
        let g = match self.bind.as_ref().unwrap().state_cat {
            Some(k) => {
                let i = self.bind.as_ref().unwrap().cats[k];
                let v = i.and_then(|i| s.get(i)).unwrap_or(&MISSING);
                nd.cats[k].values.iter().position(|c| v.is(c)).map_or(-1, |p| p as i64)
            }
            None => -1,
        };
        let sd = nd.sd_of(g);
        let mut z: Vec<f64> = (0..n).map(|j| (self.yv[j] - self.h1[j]) / sd[j]).collect();
        if let Some(c) = nd.clip.filter(|c| *c != 0.0) {
            z.iter_mut().for_each(|v| {
                *v = if *v < -c {
                    -c
                } else if *v > c {
                    c
                } else {
                    *v
                }
            });
        }
        // the CUSUM on the mean (eval/nodes.py cusum): the cumulative sum less its running minimum
        let st = &mut self.st;
        for j in 0..n {
            let ok = cov && z[j].is_finite();
            st.c[j] += if ok { z[j] - nd.k[j] } else { 0.0 };
            st.c[n + j] += if ok { -z[j] - nd.k[j] } else { 0.0 };
        }
        for j in 0..2 * n {
            st.mn[j] = min(st.mn[j], min(st.c[j], 0.0));
            st.s_m[j] = st.c[j] - st.mn[j];
        }
        let sm = st.s_m.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        st.on_m = if sm > nd.h {
            true
        } else if sm <= 0.0 {
            false
        } else {
            st.on_m
        };
        track(
            &mut self.crossings,
            st.t,
            &st.s_m,
            nd.h,
            &mut st.lz_m,
            &mut st.up_m,
            &mut st.above_m,
        );
        if nd.var() {
            // the rolling mean over var_win_s (eval/nodes.py rolling_mean), then the variance statistic
            for j in 0..n {
                let ok = cov && z[j].is_finite();
                st.rs[j] += if ok { z[j] } else { 0.0 };
                st.rc[j] += if ok { 1.0 } else { 0.0 };
            }
            st.hist.push_back((st.rs.clone(), st.rc.clone()));
            if st.hist.len() > self.win + 1 {
                st.hist.pop_front();
            }
            let (s0, c0) = st.hist.front().unwrap();
            let masked = match self.steady_n {
                None => false,
                Some(steady) => {
                    if st.prev_bin.is_some_and(|p| p != g) {
                        st.last_change = Some(st.t);
                    }
                    st.prev_bin = Some(g);
                    st.last_change.is_some_and(|lc| st.t - lc < steady)
                }
            };
            let mut uv = vec![0.0; n];
            for j in 0..n {
                let ws = st.rs[j] - s0[j];
                let wc = st.rc[j] - c0[j];
                let rm = if wc >= self.half_win {
                    ws / max(wc, 1.0)
                } else {
                    f64::NAN
                };
                let xv = z[j] - rm;
                uv[j] = xv * xv / nd.var_d[j] - 1.0;
                if nd.var_skip[j] || masked {
                    uv[j] = f64::NAN;
                }
            }
            for j in 0..n {
                let okv = cov && uv[j].is_finite();
                st.cv[j] += if okv { uv[j] - nd.kv[j] } else { 0.0 };
                st.mv[j] = min(st.mv[j], min(st.cv[j], 0.0));
                st.s_v[j] = st.cv[j] - st.mv[j];
            }
            let sv = st.s_v.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            st.on_v = if sv > nd.hv {
                true
            } else if sv <= 0.0 {
                false
            } else {
                st.on_v
            };
            track(
                &mut self.crossings,
                st.t,
                &st.s_v,
                nd.hv,
                &mut st.lz_v,
                &mut st.up_v,
                &mut st.above_v,
            );
        }
        let mut out = (st.on_m || st.on_v) && cov;
        if let Some(need) = self.confirm_n {
            st.run_len = if out { st.run_len + 1 } else { 0 };
            out = st.run_len >= need;
        }
        st.z = Some(z);
        st.cover = cov;
        st.alarm = out;
        Some(out)
    }

    /// Page's estimate of when the change behind the present alarm began (eval/nodes.py
    /// change_point): for each sum above 0 that passed its limit since its last 0, the sample after
    /// that 0; the earliest. The present sample when no sum is in such an episode.
    pub fn change_point(&self) -> i64 {
        let st = &self.st;
        let mut best: Option<i64> = None;
        for (s, lz, up) in [(&st.s_m, &st.lz_m, &st.up_m), (&st.s_v, &st.lz_v, &st.up_v)] {
            for j in 0..s.len() {
                if s[j] > 0.0 && up[j] {
                    let c = lz[j] + 1;
                    best = Some(best.map_or(c, |b| b.min(c)));
                }
            }
        }
        best.unwrap_or(st.t)
    }
}

/// np.minimum and np.maximum for numbers that are not NaN.
#[inline]
fn min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

#[inline]
fn max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// A sum that passes its limit is a crossing, with the sample after its last 0 (StreamNode._track).
fn track(cross: &mut Vec<(i64, i64)>, t: i64, s: &[f64], h: f64, lz: &mut [i64], up: &mut [bool], above: &mut [bool]) {
    for j in 0..s.len() {
        let a = s[j] > h;
        if a && !above[j] {
            cross.push((t, lz[j] + 1));
        }
        above[j] = a;
        if s[j] <= 0.0 {
            lz[j] = t;
            up[j] = false;
        }
        up[j] |= a;
    }
}
