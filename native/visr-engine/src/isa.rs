//! The instruction sets of the engine (native/README.md, "Instruction sets"). The build is x86-64-v2:
//! SSE4.2, no AVX. The kernels that gain from wider vectors have a second version in `avx2`, compiled
//! for AVX2 and FMA and chosen at run time when the processor has both. There is no AVX-512 version.
//! The setting `isa = "base"` (or VISR_ISA=base) forces the base path. `isa-scan` checks every build:
//! an AVX or AVX-512 instruction anywhere but in `isa::avx2` fails it.
//!
//! The two paths round differently (FMA rounds a product and a sum once), so both are tested against
//! the Python values, to a relative 1e-9 (tests/goldens.rs).

use crate::err;
use crate::error::Result;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Path {
    /// x86-64-v2 (SSE2 arithmetic on float64), no FMA
    Base,
    /// AVX2 vectors and FMA
    Avx2Fma,
}

impl Path {
    pub fn name(self) -> &'static str {
        match self {
            Path::Base => "base",
            Path::Avx2Fma => "avx2+fma",
        }
    }
}

/// What the settings ask for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Choice {
    /// AVX2 and FMA when the processor has both, else the base path
    Auto,
    /// the base path, whatever the processor has
    Base,
    /// AVX2 and FMA; an error when the processor lacks one of them
    Avx2,
}

impl Choice {
    pub fn parse(s: &str) -> Result<Choice> {
        match s.trim().to_ascii_lowercase().as_str() {
            "" | "auto" => Ok(Choice::Auto),
            "base" => Ok(Choice::Base),
            "avx2" | "avx2+fma" => Ok(Choice::Avx2),
            o => err!("isa {o:?}: auto, base or avx2"),
        }
    }
}

/// True when the processor (and the operating system) have AVX2 and FMA.
pub fn has_avx2_fma() -> bool {
    #[cfg(target_arch = "x86_64")]
    {
        std::arch::is_x86_feature_detected!("avx2") && std::arch::is_x86_feature_detected!("fma")
    }
    #[cfg(not(target_arch = "x86_64"))]
    {
        false
    }
}

/// The kernels of one path. A value of this type with `Avx2Fma` exists only on a processor that has
/// AVX2 and FMA (`new` checks), which makes the calls into `avx2` sound.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Kernels {
    path: Path,
}

impl Kernels {
    pub fn new(path: Path) -> Result<Kernels> {
        if path == Path::Avx2Fma && !has_avx2_fma() {
            return err!("this processor lacks AVX2 or FMA");
        }
        Ok(Kernels { path })
    }

    pub fn base() -> Kernels {
        Kernels { path: Path::Base }
    }

    pub fn select(choice: Choice) -> Result<Kernels> {
        match choice {
            Choice::Base => Ok(Kernels::base()),
            Choice::Avx2 => Kernels::new(Path::Avx2Fma),
            Choice::Auto => Ok(Kernels {
                path: if has_avx2_fma() { Path::Avx2Fma } else { Path::Base },
            }),
        }
    }

    pub fn path(&self) -> Path {
        self.path
    }

    /// out = x W, W of x.len() x out.len(), row-major. For each output the sum runs over the inputs in
    /// order; the AVX2 path computes four outputs at a time with FMA.
    #[inline]
    pub fn dense(&self, x: &[f64], w: &[f64], out: &mut [f64]) {
        assert_eq!(
            w.len(),
            x.len() * out.len(),
            "dense: W is {} values for {} x {}",
            w.len(),
            x.len(),
            out.len()
        );
        match self.path {
            Path::Base => base::dense(x, w, out),
            #[cfg(target_arch = "x86_64")]
            // SAFETY: a Kernels with Avx2Fma is only made after has_avx2_fma() (Kernels::new, select)
            Path::Avx2Fma => unsafe { avx2::dense(x, w, out) },
            #[cfg(not(target_arch = "x86_64"))]
            Path::Avx2Fma => unreachable!(),
        }
    }
}

pub mod base {
    /// out = x W (see Kernels::dense): a multiply and an add, each rounded.
    pub fn dense(x: &[f64], w: &[f64], out: &mut [f64]) {
        let n = out.len();
        out.fill(0.0);
        if n == 0 {
            return;
        }
        for (xi, row) in x.iter().zip(w.chunks_exact(n)) {
            for (o, wij) in out.iter_mut().zip(row) {
                *o += xi * wij;
            }
        }
    }
}

#[cfg(target_arch = "x86_64")]
pub mod avx2 {
    /// out = x W (see Kernels::dense) with a fused multiply-add, rounded once.
    ///
    /// # Safety
    /// The processor must have AVX2 and FMA.
    #[target_feature(enable = "avx2,fma")]
    pub unsafe fn dense(x: &[f64], w: &[f64], out: &mut [f64]) {
        let n = out.len();
        out.fill(0.0);
        if n == 0 {
            return;
        }
        for (xi, row) in x.iter().zip(w.chunks_exact(n)) {
            for (o, wij) in out.iter_mut().zip(row) {
                *o = xi.mul_add(*wij, *o);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_paths_compute_the_product() {
        let x = [1.5, -2.0, 0.25];
        let w = [
            1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0,
        ];
        let want = [
            1.5 - 12.0 + 2.75,
            3.0 - 14.0 + 3.0,
            4.5 - 16.0 + 3.25,
            6.0 - 18.0 + 3.5,
            7.5 - 20.0 + 3.75,
        ];
        let mut out = [0.0; 5];
        Kernels::base().dense(&x, &w, &mut out);
        assert_eq!(out, want);
        if has_avx2_fma() {
            let mut o2 = [9.0; 5];
            Kernels::new(Path::Avx2Fma).unwrap().dense(&x, &w, &mut o2);
            assert_eq!(o2, want);
        }
    }

    #[test]
    fn the_switch_forces_the_base_path() {
        assert_eq!(
            Kernels::select(Choice::parse("base").unwrap()).unwrap().path(),
            Path::Base
        );
        assert_eq!(
            Kernels::select(Choice::Auto).unwrap().path() == Path::Avx2Fma,
            has_avx2_fma()
        );
        assert!(Choice::parse("avx512").is_err());
    }
}
