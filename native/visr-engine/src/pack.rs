//! The model pack (native/README.md, "The model pack"): what export_pack.py writes from the live pack
//! of a campaign (build_pack in live/engine.py). Every constant of the engine comes from here.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::Arc;

use ndarray::{Array1, Array2};
use serde::Deserialize;
use serde_json::Value;

use crate::err;
use crate::error::{Context, Result};
use crate::neutral::Reader;

pub const FORMAT: &str = "visr-pack";
pub const VERSION: u64 = 1;

/// The constants that the Python engine keeps in its code (eval/nodes.py, live/engine.py).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Constants {
    /// seconds between two samples
    pub sample_s: f64,
    /// the window of the rolling mean of the variance statistic
    pub var_win_s: f64,
    /// the margin of the coverage test, a share of each feature's training range on each side
    pub coverage_margin: f64,
    /// the samples at the start of a run that only warm the filters
    pub lead_s: f64,
    /// the root of an incident is named this long after it opens
    pub post_s: f64,
    /// an incident closes after this long with no alarm
    pub hold_s: f64,
    /// the samples kept for the counterfactual
    pub keep_s: f64,
    pub graph_windows_s: Vec<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Rule {
    /// two-sided CUSUM on the mean, one k for every output
    Cusum,
    /// two-sided CUSUM on the mean, k per output from the slow bias of the calibration days
    Cusumk,
    /// cusumk, and a one-sided CUSUM on the variance of each residual
    Cusumkv,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cats {
    pub tag: String,
    pub values: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct LayerEntry {
    w: String,
    b: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct NormEntry {
    xm: String,
    xs: String,
    ym: String,
    ys: String,
    xlo: String,
    xhi: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ModelEntry {
    inputs: Vec<String>,
    outputs: Vec<String>,
    taus: Vec<f64>,
    hidden: Vec<usize>,
    mlp: Vec<LayerEntry>,
    linear: LayerEntry,
    norm: NormEntry,
    cov_cols: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ArraysEntry {
    k: String,
    sd_all: String,
    sd: Vec<String>,
    kv: Option<String>,
    var_d: Option<String>,
    var_skip: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct NodeEntry {
    name: String,
    rule: Rule,
    doc: Value,
    num_in: Vec<String>,
    cats: Vec<Cats>,
    state_cmd: Option<String>,
    delta_in: Vec<String>,
    counter_resets: bool,
    out_names: Vec<String>,
    out_cats: Vec<Cats>,
    clip: Option<f64>,
    confirm_s: f64,
    var_steady: f64,
    h: f64,
    hv: Option<f64>,
    sd_bins: Vec<i64>,
    arrays: ArraysEntry,
    model: ModelEntry,
    #[allow(dead_code)]
    info: Value,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format: String,
    version: u64,
    campaign: Option<String>,
    #[allow(dead_code)]
    calib_windows: Option<Value>,
    #[allow(dead_code)]
    source: Value,
    #[allow(dead_code)]
    exported_by: String,
    float: String,
    constants: Constants,
    wiring: Value,
    nodes: BTreeMap<String, NodeEntry>,
    sig2: Option<BTreeMap<String, String>>,
    #[allow(dead_code)]
    meta: Value,
    #[allow(dead_code)]
    files: BTreeMap<String, String>,
}

/// One dense layer: out = x W + b, W of n_in x n_out (row-major).
#[derive(Debug, Clone)]
pub struct Dense {
    pub w: Array2<f64>,
    pub b: Array1<f64>,
}

impl Dense {
    pub fn n_in(&self) -> usize {
        self.w.nrows()
    }
    pub fn n_out(&self) -> usize {
        self.w.ncols()
    }
}

/// The node model (twin/node_model.py NodeModel): the features are the inputs and each input's
/// first-order filters (input-major); y = MLP(xn) + xn L + l on the standardized features xn, then
/// scaled back. tanh on every MLP layer but the last.
#[derive(Debug, Clone)]
pub struct Model {
    pub inputs: Vec<String>,
    pub outputs: Vec<String>,
    pub taus: Vec<f64>,
    pub mlp: Vec<Dense>,
    pub linear: Dense,
    pub xm: Array1<f64>,
    pub xs: Array1<f64>,
    pub ym: Array1<f64>,
    pub ys: Array1<f64>,
    pub xlo: Array1<f64>,
    pub xhi: Array1<f64>,
    /// the feature columns that count for coverage (None: all of them)
    pub cov_cols: Option<Vec<bool>>,
}

impl Model {
    pub fn n_in(&self) -> usize {
        self.inputs.len()
    }
    pub fn n_x(&self) -> usize {
        self.inputs.len() * (1 + self.taus.len())
    }
    pub fn n_out(&self) -> usize {
        self.outputs.len()
    }
}

/// One trained node of the pack (eval/nodes.py Node, as live/engine.py StreamNode reads it).
#[derive(Debug, Clone)]
pub struct NodePack {
    pub name: String,
    pub rule: Rule,
    pub measures: Vec<String>,
    pub num_in: Vec<String>,
    pub cats: Vec<Cats>,
    pub state_cmd: Option<String>,
    pub delta_in: Vec<String>,
    pub counter_resets: bool,
    pub out_names: Vec<String>,
    pub out_cats: Vec<Cats>,
    pub clip: Option<f64>,
    pub confirm_s: f64,
    pub var_steady: f64,
    pub h: f64,
    pub k: Vec<f64>,
    pub hv: f64,
    pub kv: Vec<f64>,
    pub var_d: Vec<f64>,
    pub var_skip: Vec<bool>,
    pub sd_all: Vec<f64>,
    pub sd: Vec<(i64, Vec<f64>)>,
    pub model: Model,
}

impl NodePack {
    pub fn n_out(&self) -> usize {
        self.out_names.len()
    }

    pub fn var(&self) -> bool {
        self.rule == Rule::Cusumkv
    }

    /// The residual spread of PLC state g (the calibration's spread over all states when g has none).
    pub fn sd_of(&self, g: i64) -> &[f64] {
        self.sd
            .iter()
            .find(|(b, _)| *b == g)
            .map_or(&self.sd_all[..], |(_, v)| &v[..])
    }
}

#[derive(Debug, Clone)]
pub struct Pack {
    pub manifest_sha256: String,
    pub campaign: Option<String>,
    pub constants: Constants,
    pub wiring: Value,
    pub nodes: Vec<Arc<NodePack>>,
    pub sig2: Option<BTreeMap<String, Vec<f64>>>,
}

impl Pack {
    /// Reads a pack folder. Every file's SHA-256 is checked, and every array's shape against the
    /// node's inputs, filters and outputs.
    pub fn load(dir: impl AsRef<Path>) -> Result<Pack> {
        let r = Reader::open(dir.as_ref(), FORMAT)?;
        let m: Manifest =
            serde_json::from_value(r.manifest().clone()).ctx(|| format!("{}/manifest.json", r.dir().display()))?;
        if m.format != FORMAT || m.version != VERSION {
            return err!(
                "{}: {} version {}, {FORMAT} version {VERSION} expected",
                r.dir().display(),
                m.format,
                m.version
            );
        }
        if m.float != "float64" {
            return err!("{}: numbers in {}, float64 expected", r.dir().display(), m.float);
        }
        let c = &m.constants;
        if !(c.sample_s > 0.0 && c.var_win_s > 0.0 && c.coverage_margin >= 0.0) {
            return err!("{}: constants out of range: {c:?}", r.dir().display());
        }
        let mut nodes = Vec::new();
        for (name, e) in m.nodes {
            nodes.push(Arc::new(node(&r, &name, e).ctx(|| format!("pack node {name}"))?));
        }
        let sig2 = match m.sig2 {
            None => None,
            Some(s) => Some(
                s.into_iter()
                    .map(|(n, f)| Ok((n, r.vec_f64(&f, None)?)))
                    .collect::<Result<_>>()?,
            ),
        };
        Ok(Pack {
            manifest_sha256: r.manifest_sha256().to_string(),
            campaign: m.campaign,
            constants: m.constants,
            wiring: m.wiring,
            nodes,
            sig2,
        })
    }

    pub fn node(&self, name: &str) -> Option<&Arc<NodePack>> {
        self.nodes.iter().find(|n| n.name == name)
    }
}

fn dense(r: &Reader, e: &LayerEntry, n_in: Option<usize>, n_out: Option<usize>) -> Result<Dense> {
    let (a, b, w) = r.mat_f64(&e.w, n_in, n_out)?;
    let bias = r.vec_f64(&e.b, Some(b))?;
    Ok(Dense {
        w: Array2::from_shape_vec((a, b), w).expect("shape checked"),
        b: Array1::from(bias),
    })
}

fn node(r: &Reader, name: &str, e: NodeEntry) -> Result<NodePack> {
    if e.name != name {
        return err!("the entry is named {}", e.name);
    }
    let me = &e.model;
    if me.taus.is_empty() || me.taus.iter().any(|t| t.is_nan() || *t <= 0.0) {
        return err!("filter time constants {:?}", me.taus);
    }
    let n_in = e.num_in.len() + e.cats.iter().map(|c| c.values.len()).sum::<usize>() + e.delta_in.len();
    if me.inputs.len() != n_in {
        return err!(
            "{} model inputs, {n_in} from the numeric, text and change inputs",
            me.inputs.len()
        );
    }
    let n_x = n_in * (1 + me.taus.len());
    let n_out = e.out_names.len();
    if me.outputs != e.out_names {
        return err!(
            "the model's outputs {:?} differ from the node's {:?}",
            me.outputs,
            e.out_names
        );
    }
    let measures: Vec<String> = match e.doc.get("measures").and_then(Value::as_array) {
        Some(a) => a
            .iter()
            .map(|v| v.as_str().map(str::to_string))
            .collect::<Option<_>>()
            .unwrap_or_default(),
        None => return err!("its documents have no measures"),
    };
    let n_y: usize = measures
        .iter()
        .map(|t| e.out_cats.iter().find(|c| &c.tag == t).map_or(1, |c| c.values.len()))
        .sum();
    if n_y != n_out {
        return err!("{n_y} outputs from the measures, {n_out} output names");
    }
    if let Some(sc) = &e.state_cmd {
        if !e.cats.iter().any(|c| &c.tag == sc) {
            return err!("the state command {sc} is not a text input");
        }
    }
    if me.mlp.is_empty() || me.mlp.len() != me.hidden.len() + 1 {
        return err!("{} network layers for hidden sizes {:?}", me.mlp.len(), me.hidden);
    }
    let mut mlp = Vec::new();
    let mut width = n_x;
    for (i, l) in me.mlp.iter().enumerate() {
        let out = if i < me.hidden.len() { me.hidden[i] } else { n_out };
        mlp.push(dense(r, l, Some(width), Some(out)).ctx(|| format!("layer {i}"))?);
        width = out;
    }
    let linear = dense(r, &me.linear, Some(n_x), Some(n_out)).ctx(|| "linear path".to_string())?;
    let v = |f: &str, n: usize| r.vec_f64(f, Some(n)).map(Array1::from);
    let model = Model {
        inputs: me.inputs.clone(),
        outputs: me.outputs.clone(),
        taus: me.taus.clone(),
        mlp,
        linear,
        xm: v(&me.norm.xm, n_x)?,
        xs: v(&me.norm.xs, n_x)?,
        ym: v(&me.norm.ym, n_out)?,
        ys: v(&me.norm.ys, n_out)?,
        xlo: v(&me.norm.xlo, n_x)?,
        xhi: v(&me.norm.xhi, n_x)?,
        cov_cols: me.cov_cols.as_ref().map(|f| r.vec_bool(f, Some(n_x))).transpose()?,
    };
    if e.sd_bins.len() != e.arrays.sd.len() {
        return err!("{} state bins, {} spreads", e.sd_bins.len(), e.arrays.sd.len());
    }
    let sd = e
        .sd_bins
        .iter()
        .zip(&e.arrays.sd)
        .map(|(g, f)| Ok((*g, r.vec_f64(f, Some(n_out))?)))
        .collect::<Result<Vec<_>>>()?;
    let var = e.rule == Rule::Cusumkv;
    let need = |f: &Option<String>, what: &str| -> Result<String> {
        match f {
            Some(f) => Ok(f.clone()),
            None => err!("rule cusumkv without {what}"),
        }
    };
    let (hv, kv, var_d, var_skip) = if var {
        let hv = match e.hv {
            Some(h) => h,
            None => return err!("rule cusumkv without hv"),
        };
        (
            hv,
            r.vec_f64(&need(&e.arrays.kv, "kv")?, Some(n_out))?,
            r.vec_f64(&need(&e.arrays.var_d, "var_d")?, Some(n_out))?,
            r.vec_bool(&need(&e.arrays.var_skip, "var_skip")?, Some(n_out))?,
        )
    } else {
        (f64::INFINITY, vec![0.0; n_out], vec![1.0; n_out], vec![false; n_out])
    };
    Ok(NodePack {
        name: e.name,
        rule: e.rule,
        measures,
        num_in: e.num_in,
        cats: e.cats,
        state_cmd: e.state_cmd,
        delta_in: e.delta_in,
        counter_resets: e.counter_resets,
        out_names: e.out_names,
        out_cats: e.out_cats,
        clip: e.clip,
        confirm_s: e.confirm_s,
        var_steady: e.var_steady,
        h: e.h,
        k: r.vec_f64(&e.arrays.k, Some(n_out))?,
        hv,
        kv,
        var_d,
        var_skip,
        sd_all: r.vec_f64(&e.arrays.sd_all, Some(n_out))?,
        sd,
        model,
    })
}
