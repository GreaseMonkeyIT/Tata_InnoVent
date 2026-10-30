//! visr-engine (native/README.md). Step N1: it reads its settings, selects its instruction set, loads
//! and checks the model pack, and takes over the node states of its state folder. The source (the
//! plant service), the HTTP service with /healthz and the incidents come in the next steps.
//!
//!     visr-engine [--settings FILE] check     the settings, the pack and the states; exit 0 when sound
//!     visr-engine --version
//!
//! The log goes to standard output, one line per event: `level=... msg=...`.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use visr_engine::isa::{has_avx2_fma, Kernels};
use visr_engine::settings::Settings;
use visr_engine::{Node, NodeState, Pack, Result};

fn log(level: &str, msg: &str) {
    println!("level={level} msg={msg:?}");
}

/// The folder of one node's state inside the engine's state folder.
fn state_path(dir: &Path, node: &str) -> PathBuf {
    let safe: String = node
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || "._-".contains(c) {
                c
            } else {
                '_'
            }
        })
        .collect();
    dir.join("nodes").join(safe)
}

fn check(settings: Option<&Path>) -> Result<()> {
    let s = Settings::load(settings, &|k| std::env::var(k).ok())?;
    for src in &s.sources {
        log("info", &format!("setting {src}"));
    }
    let kern = Kernels::select(s.isa)?;
    log(
        "info",
        &format!(
            "isa {} (processor has avx2+fma: {})",
            kern.path().name(),
            has_avx2_fma()
        ),
    );
    let pack = Pack::load(&s.pack)?;
    log(
        "info",
        &format!(
            "pack {} campaign {} nodes {} manifest sha256 {}",
            s.pack.display(),
            pack.campaign.as_deref().unwrap_or("-"),
            pack.nodes.len(),
            &pack.manifest_sha256[..16]
        ),
    );
    let (mut taken, mut fresh) = (0, 0);
    for nd in &pack.nodes {
        let mut node = Node::new(nd.clone(), &pack.constants, kern);
        let dir = state_path(&s.state_dir, &nd.name);
        if dir.join("manifest.json").exists() {
            let st = NodeState::load(&dir, nd, &pack.manifest_sha256, node.hist_max())?;
            node.set_state(st)?;
            taken += 1;
        } else {
            fresh += 1;
        }
    }
    log(
        "info",
        &format!(
            "node states taken over {taken}, new {fresh} (state folder {})",
            s.state_dir.display()
        ),
    );
    Ok(())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut settings = None;
    let mut cmd = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--version" | "-V" => {
                println!("visr-engine {}", env!("CARGO_PKG_VERSION"));
                return ExitCode::SUCCESS;
            }
            "--help" | "-h" => {
                println!("visr-engine [--settings FILE] check\n  settings: VISR_SETTINGS, VISR_PACK, VISR_STATE_DIR, VISR_ISA (auto|base|avx2)");
                return ExitCode::SUCCESS;
            }
            "--settings" => settings = it.next().map(PathBuf::from),
            c if cmd.is_none() => cmd = Some(c.to_string()),
            o => {
                log("error", &format!("unknown argument {o}"));
                return ExitCode::from(2);
            }
        }
    }
    let r = match cmd.as_deref().unwrap_or("check") {
        "check" => check(settings.as_deref()),
        o => {
            log("error", &format!("unknown command {o}: check"));
            return ExitCode::from(2);
        }
    };
    match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            log("error", &e.0);
            ExitCode::FAILURE
        }
    }
}
