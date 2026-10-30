//! isa-scan: the disassembly scan of a build (native/README.md, "Instruction sets").
//!
//!     isa-scan [--allow PATH]... BINARY...
//!
//! It disassembles each binary with objdump and finds every AVX instruction (VEX: the v-prefixed
//! mnemonics and any ymm register) and every AVX-512 instruction (zmm, the mask registers k0 to k7,
//! xmm16 to xmm31 and ymm16 to ymm31, embedded broadcasts, the k-mask mnemonics). Each AVX instruction
//! must be inside a function of the AVX2 path: a function whose demangled name holds an --allow path
//! (default visr_engine::isa::avx2::). There must be no AVX-512 instruction at all. Exit status 1
//! otherwise, with the functions and their first offending instruction.

use std::collections::BTreeMap;
use std::process::{Command, ExitCode};

const DEFAULT_ALLOW: &str = "visr_engine::isa::avx2::";
/// v-mnemonics that are not AVX (virtualization and segment checks)
const NOT_AVX: &[&str] = &[
    "verr", "verw", "vmcall", "vmclear", "vmfunc", "vmlaunch", "vmload", "vmmcall", "vmptrld", "vmptrst", "vmread",
    "vmresume", "vmrun", "vmsave", "vmwrite", "vmxoff", "vmxon",
];
const MASK_OPS: &[&str] = &[
    "kmov", "kand", "kor", "kxor", "knot", "kshift", "ktest", "kadd", "kunpck", "kxnor",
];

#[derive(Default)]
struct Func {
    avx: usize,
    avx512: usize,
    first: Option<String>,
}

fn high_reg(ops: &str, prefix: &str) -> bool {
    // %xmm16 .. %xmm31, %ymm16 .. %ymm31
    ops.match_indices(prefix).any(|(i, _)| {
        let d: String = ops[i + prefix.len()..]
            .chars()
            .take_while(|c| c.is_ascii_digit())
            .collect();
        d.parse::<u32>().is_ok_and(|n| (16..32).contains(&n))
    })
}

fn mask_reg(ops: &str) -> bool {
    ops.match_indices("%k")
        .any(|(i, _)| ops[i + 2..].chars().next().is_some_and(|c| ('0'..='7').contains(&c)))
}

/// (AVX, AVX-512) for one instruction.
fn classify(mnemonic: &str, ops: &str) -> (bool, bool) {
    let avx512 = ops.contains("%zmm")
        || mask_reg(ops)
        || high_reg(ops, "%xmm")
        || high_reg(ops, "%ymm")
        || ops.contains("{1to")
        || MASK_OPS.iter().any(|m| mnemonic.starts_with(m));
    let avx = avx512
        || ops.contains("%ymm")
        || (mnemonic.starts_with('v') && !NOT_AVX.iter().any(|m| mnemonic.starts_with(m)));
    (avx, avx512)
}

fn scan(bin: &str, allow: &[String]) -> Result<bool, String> {
    let out = Command::new("objdump")
        .args(["-d", "--no-show-raw-insn", "-w", bin])
        .output()
        .map_err(|e| format!("objdump: {e} (binutils must be installed)"))?;
    if !out.status.success() {
        return Err(format!(
            "objdump {bin}: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    let text = String::from_utf8_lossy(&out.stdout);
    let mut funcs: BTreeMap<String, Func> = BTreeMap::new();
    let mut cur = String::from("(no symbol)");
    let mut n_insn = 0usize;
    for line in text.lines() {
        if let (Some(a), true) = (line.find('<'), line.ends_with(">:")) {
            let sym = &line[a + 1..line.len() - 2];
            cur = format!("{:#}", rustc_demangle::demangle(sym));
            continue;
        }
        // "   1234:\tvfmadd231pd %ymm1,%ymm2,%ymm0"
        let Some((addr, rest)) = line.split_once(":\t") else {
            continue;
        };
        if !addr.trim().chars().all(|c| c.is_ascii_hexdigit()) || addr.trim().is_empty() {
            continue;
        }
        let rest = rest.trim();
        let (mnemonic, ops) = rest.split_once(char::is_whitespace).unwrap_or((rest, ""));
        let mnemonic = mnemonic.trim_start_matches("{vex}").trim();
        if mnemonic.is_empty() {
            continue;
        }
        n_insn += 1;
        let (avx, avx512) = classify(mnemonic, ops);
        if avx {
            let f = funcs.entry(cur.clone()).or_default();
            f.avx += 1;
            f.avx512 += avx512 as usize;
            f.first.get_or_insert_with(|| format!("{}: {rest}", addr.trim()));
        }
    }
    let allowed = |name: &str| allow.iter().any(|a| name.contains(a.as_str()));
    let (mut in_path, mut outside, mut avx512) = (0, 0, 0);
    let mut bad = Vec::new();
    for (name, f) in &funcs {
        avx512 += f.avx512;
        if allowed(name) {
            in_path += f.avx;
        } else {
            outside += f.avx;
        }
        if f.avx512 > 0 || !allowed(name) {
            bad.push(format!(
                "    {name}: {} AVX, {} AVX-512; first {}",
                f.avx,
                f.avx512,
                f.first.as_deref().unwrap_or("")
            ));
        }
    }
    let path_fns: Vec<&String> = funcs.keys().filter(|n| allowed(n)).collect();
    println!(
        "{bin}: {n_insn} instructions; AVX {} in the AVX2 path ({} functions), {outside} outside it; AVX-512 {avx512}",
        in_path,
        path_fns.len()
    );
    for n in &path_fns {
        println!("    AVX2 path: {n} ({} instructions)", funcs[*n].avx);
    }
    if !bad.is_empty() {
        println!("  FAILS:");
        bad.iter().for_each(|b| println!("{b}"));
    }
    Ok(bad.is_empty())
}

fn main() -> ExitCode {
    let mut allow = Vec::new();
    let mut bins = Vec::new();
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--allow" => allow.extend(it.next()),
            "-h" | "--help" => {
                println!("isa-scan [--allow PATH]... BINARY...");
                return ExitCode::SUCCESS;
            }
            _ => bins.push(a),
        }
    }
    if allow.is_empty() {
        allow.push(DEFAULT_ALLOW.to_string());
    }
    if bins.is_empty() {
        eprintln!("isa-scan [--allow PATH]... BINARY...");
        return ExitCode::from(2);
    }
    let mut ok = true;
    for b in &bins {
        match scan(b, &allow) {
            Ok(good) => ok &= good,
            Err(e) => {
                eprintln!("{e}");
                ok = false;
            }
        }
    }
    if ok {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_the_instruction_sets() {
        assert_eq!(classify("vfmadd231pd", "%ymm1,%ymm2,%ymm0"), (true, false));
        assert_eq!(classify("vmovsd", "(%rax),%xmm0"), (true, false));
        assert_eq!(classify("vaddpd", "%zmm1,%zmm2,%zmm0"), (true, true));
        assert_eq!(classify("vmovapd", "%xmm17,%xmm0"), (true, true));
        assert_eq!(classify("vaddpd", "(%rax){1to8},%zmm1,%zmm0"), (true, true));
        assert_eq!(classify("kmovw", "%eax,%k1"), (true, true));
        assert_eq!(classify("vmovupd", "%xmm1,(%rdi){%k1}"), (true, true));
        assert_eq!(classify("addsd", "%xmm1,%xmm0"), (false, false));
        assert_eq!(classify("movupd", "%xmm1,(%rdi)"), (false, false));
        assert_eq!(classify("vmcall", ""), (false, false));
        assert_eq!(classify("verw", "%ax"), (false, false));
    }
}
