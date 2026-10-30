//! The neutral folder of the pack, the goldens and the node state (native/README.md): manifest.json
//! and one .npy file per array, with the SHA-256 of each file in the manifest's "files". A reader
//! checks every hash when it opens the folder and again when it reads a file; a file that the
//! manifest does not list is never read.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Component, Path, PathBuf};

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::err;
use crate::error::{Context, Error, Result};
use crate::npy::{self, Data, Npy};

pub fn sha256_hex(b: &[u8]) -> String {
    let d = Sha256::digest(b);
    d.iter().map(|x| format!("{x:02x}")).collect()
}

/// A path inside the folder: relative, no "..", no root.
fn inside(dir: &Path, rel: &str) -> Result<PathBuf> {
    let p = Path::new(rel);
    if rel.is_empty() || !p.components().all(|c| matches!(c, Component::Normal(_))) {
        return err!("{rel:?} is not a plain relative path");
    }
    Ok(dir.join(p))
}

#[derive(Debug)]
pub struct Reader {
    dir: PathBuf,
    manifest: Value,
    manifest_sha256: String,
    files: BTreeMap<String, String>,
}

impl Reader {
    /// Opens a folder and checks the hash of every file the manifest lists.
    pub fn open(dir: impl AsRef<Path>, format: &str) -> Result<Reader> {
        let dir = dir.as_ref().to_path_buf();
        let raw = fs::read(dir.join("manifest.json")).ctx(|| format!("{}/manifest.json", dir.display()))?;
        let manifest: Value = serde_json::from_slice(&raw).ctx(|| format!("{}/manifest.json", dir.display()))?;
        let got = manifest.get("format").and_then(Value::as_str).unwrap_or("");
        if got != format {
            return err!("{}: the format is {got:?}, {format:?} expected", dir.display());
        }
        let mut files = BTreeMap::new();
        if let Some(f) = manifest.get("files").and_then(Value::as_object) {
            for (rel, h) in f {
                let h = h
                    .as_str()
                    .ok_or_else(|| Error(format!("{}: the hash of {rel} is not text", dir.display())))?;
                files.insert(rel.clone(), h.to_string());
            }
        }
        let r = Reader {
            manifest_sha256: sha256_hex(&raw),
            dir,
            manifest,
            files,
        };
        for rel in r.files.keys() {
            r.bytes(rel)?;
        }
        Ok(r)
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn manifest(&self) -> &Value {
        &self.manifest
    }

    /// The SHA-256 of manifest.json: the identity of the folder (it lists the hash of every file).
    pub fn manifest_sha256(&self) -> &str {
        &self.manifest_sha256
    }

    /// The bytes of a listed file, after its hash check.
    pub fn bytes(&self, rel: &str) -> Result<Vec<u8>> {
        let want = match self.files.get(rel) {
            Some(h) => h,
            None => return err!("{}: {rel} is not listed in the manifest", self.dir.display()),
        };
        let path = inside(&self.dir, rel)?;
        let b = fs::read(&path).ctx(|| path.display().to_string())?;
        let got = sha256_hex(&b);
        if &got != want {
            return err!(
                "{}: SHA-256 {} differs from the manifest's {}",
                path.display(),
                &got[..16],
                &want[..want.len().min(16)]
            );
        }
        Ok(b)
    }

    pub fn array(&self, rel: &str) -> Result<Npy> {
        npy::parse(&self.bytes(rel)?).ctx(|| format!("{}/{rel}", self.dir.display()))
    }

    /// A float64 vector of length n (n None: any length).
    pub fn vec_f64(&self, rel: &str, n: Option<usize>) -> Result<Vec<f64>> {
        let a = self.array(rel)?;
        let v = a.f64().ctx(|| rel.to_string())?.to_vec();
        if a.shape.len() != 1 || n.is_some_and(|n| n != v.len()) {
            return err!(
                "{rel}: shape {:?}, ({},) expected",
                a.shape,
                n.map_or("n".into(), |n| n.to_string())
            );
        }
        Ok(v)
    }

    pub fn vec_bool(&self, rel: &str, n: Option<usize>) -> Result<Vec<bool>> {
        let a = self.array(rel)?;
        let v = a.bools().ctx(|| rel.to_string())?.to_vec();
        if a.shape.len() != 1 || n.is_some_and(|n| n != v.len()) {
            return err!(
                "{rel}: shape {:?}, ({},) expected",
                a.shape,
                n.map_or("n".into(), |n| n.to_string())
            );
        }
        Ok(v)
    }

    pub fn vec_i64(&self, rel: &str, n: Option<usize>) -> Result<Vec<i64>> {
        let a = self.array(rel)?;
        let v = a.ints().ctx(|| rel.to_string())?;
        if a.shape.len() != 1 || n.is_some_and(|n| n != v.len()) {
            return err!(
                "{rel}: shape {:?}, ({},) expected",
                a.shape,
                n.map_or("n".into(), |n| n.to_string())
            );
        }
        Ok(v)
    }

    /// A float64 matrix of r x c (None: any), row-major.
    pub fn mat_f64(&self, rel: &str, r: Option<usize>, c: Option<usize>) -> Result<(usize, usize, Vec<f64>)> {
        let a = self.array(rel)?;
        let v = a.f64().ctx(|| rel.to_string())?.to_vec();
        if a.shape.len() != 2 || r.is_some_and(|r| r != a.shape[0]) || c.is_some_and(|c| c != a.shape[1]) {
            return err!("{rel}: shape {:?}, ({:?}, {:?}) expected", a.shape, r, c);
        }
        Ok((a.shape[0], a.shape[1], v))
    }
}

/// Writes a neutral folder: the arrays first, then the manifest with their hashes.
pub struct Writer {
    dir: PathBuf,
    files: BTreeMap<String, String>,
}

impl Writer {
    pub fn create(dir: impl AsRef<Path>) -> Result<Writer> {
        let dir = dir.as_ref().to_path_buf();
        fs::create_dir_all(&dir).ctx(|| dir.display().to_string())?;
        Ok(Writer {
            dir,
            files: BTreeMap::new(),
        })
    }

    pub fn array(&mut self, rel: &str, shape: &[usize], data: Data) -> Result<String> {
        let b = npy::write(shape, &data).ctx(|| rel.to_string())?;
        let path = inside(&self.dir, rel)?;
        if let Some(p) = path.parent() {
            fs::create_dir_all(p).ctx(|| p.display().to_string())?;
        }
        fs::write(&path, &b).ctx(|| path.display().to_string())?;
        self.files.insert(rel.to_string(), sha256_hex(&b));
        Ok(rel.to_string())
    }

    /// Writes manifest.json (with "files") through a temporary file and a rename, so a reader never
    /// sees half a manifest. Returns its SHA-256.
    pub fn finish(self, mut manifest: Value) -> Result<String> {
        let files: serde_json::Map<String, Value> =
            self.files.into_iter().map(|(k, v)| (k, Value::String(v))).collect();
        manifest["files"] = Value::Object(files);
        let mut raw = serde_json::to_vec_pretty(&manifest)?;
        raw.push(b'\n');
        let tmp = self.dir.join("manifest.json.tmp");
        fs::write(&tmp, &raw).ctx(|| tmp.display().to_string())?;
        fs::rename(&tmp, self.dir.join("manifest.json")).ctx(|| self.dir.display().to_string())?;
        Ok(sha256_hex(&raw))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("visr-neutral-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn a_changed_file_is_refused() {
        let d = tmp("hash");
        let mut w = Writer::create(&d).unwrap();
        w.array("a/x.npy", &[3], Data::F64(vec![1.0, 2.0, 3.0])).unwrap();
        w.finish(json!({"format": "t", "version": 1})).unwrap();
        let r = Reader::open(&d, "t").unwrap();
        assert_eq!(r.vec_f64("a/x.npy", Some(3)).unwrap(), vec![1.0, 2.0, 3.0]);
        assert!(r.array("b.npy").is_err()); // not listed
        assert!(Reader::open(&d, "other").is_err()); // another format
        fs::write(
            d.join("a/x.npy"),
            npy::write(&[3], &Data::F64(vec![1.0, 2.0, 4.0])).unwrap(),
        )
        .unwrap();
        assert!(Reader::open(&d, "t")
            .unwrap_err()
            .0
            .contains("differs from the manifest"));
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn paths_stay_inside_the_folder() {
        let d = tmp("paths");
        for bad in ["../x.npy", "/etc/x.npy", "", "a/../../x.npy"] {
            assert!(inside(&d, bad).is_err(), "{bad}");
        }
        assert!(inside(&d, "nodes/0001/k.npy").is_ok());
    }
}
