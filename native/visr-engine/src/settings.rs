//! The engine's settings: a TOML file, then the environment (VISR_*), which wins. Ready for a
//! container: the file is mounted read-only, the state folder is a volume, the log goes to stdout.
//!
//! ```toml
//! pack = "/pack"            # the model pack folder (export_pack.py)
//! state_dir = "/state"      # the engine's state: one folder, written by the engine only
//! isa = "auto"              # auto | base | avx2
//! ```
//!
//! Environment: VISR_SETTINGS (the file), VISR_PACK, VISR_STATE_DIR, VISR_ISA.

use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::err;
use crate::error::{Context, Error, Result};
use crate::isa::Choice;

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct File {
    pack: Option<PathBuf>,
    state_dir: Option<PathBuf>,
    isa: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Settings {
    pub pack: PathBuf,
    pub state_dir: PathBuf,
    pub isa: Choice,
    /// where each value came from, for the log
    pub sources: Vec<String>,
}

impl Settings {
    /// The settings from `file` (or VISR_SETTINGS) and the environment `env` (a lookup, so tests can
    /// pass their own).
    pub fn load(file: Option<&Path>, env: &dyn Fn(&str) -> Option<String>) -> Result<Settings> {
        let path = file
            .map(Path::to_path_buf)
            .or_else(|| env("VISR_SETTINGS").map(PathBuf::from));
        let mut sources = Vec::new();
        let f: File = match &path {
            Some(p) => {
                let text = std::fs::read_to_string(p).ctx(|| p.display().to_string())?;
                sources.push(format!("file {}", p.display()));
                toml::from_str(&text).map_err(|e| Error(format!("{}: {e}", p.display())))?
            }
            None => File::default(),
        };
        let pick = |key: &str, var: &str, from_file: Option<String>, sources: &mut Vec<String>| -> Option<String> {
            match env(var) {
                Some(v) => {
                    sources.push(format!("{key} from {var}"));
                    Some(v)
                }
                None => from_file,
            }
        };
        let pack = pick(
            "pack",
            "VISR_PACK",
            f.pack.map(|p| p.display().to_string()),
            &mut sources,
        );
        let state_dir = pick(
            "state_dir",
            "VISR_STATE_DIR",
            f.state_dir.map(|p| p.display().to_string()),
            &mut sources,
        );
        let isa = pick("isa", "VISR_ISA", f.isa, &mut sources);
        let pack = match pack {
            Some(p) => PathBuf::from(p),
            None => return err!("no pack: set pack in the settings file or VISR_PACK"),
        };
        let state_dir = match state_dir {
            Some(p) => PathBuf::from(p),
            None => return err!("no state folder: set state_dir in the settings file or VISR_STATE_DIR"),
        };
        Ok(Settings {
            pack,
            state_dir,
            isa: Choice::parse(isa.as_deref().unwrap_or("auto"))?,
            sources,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn the_environment_wins_over_the_file() {
        let dir = std::env::temp_dir().join(format!("visr-settings-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("engine.toml");
        std::fs::write(&f, "pack = \"/pack\"\nstate_dir = \"/state\"\nisa = \"avx2\"\n").unwrap();
        let env: HashMap<&str, &str> = [("VISR_ISA", "base")].into();
        let s = Settings::load(Some(&f), &|k| env.get(k).map(|v| v.to_string())).unwrap();
        assert_eq!(
            (s.pack, s.state_dir, s.isa),
            (PathBuf::from("/pack"), PathBuf::from("/state"), Choice::Base)
        );
        std::fs::write(&f, "pack = \"/pack\"\nplant = \"x\"\n").unwrap();
        assert!(Settings::load(Some(&f), &|_| None).is_err()); // an unknown key
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
