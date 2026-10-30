//! The .npy format (NumPy's format version 1 to 3): one array, little-endian, C order. The pack, the
//! goldens and the node state keep each array in one such file (native/README.md).

use crate::err;
use crate::error::Result;

const MAGIC: &[u8] = b"\x93NUMPY";

#[derive(Debug, Clone, PartialEq)]
pub enum Data {
    F64(Vec<f64>),
    Bool(Vec<bool>),
    I64(Vec<i64>),
    I32(Vec<i32>),
    I16(Vec<i16>),
    U8(Vec<u8>),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Npy {
    pub shape: Vec<usize>,
    pub data: Data,
}

impl Npy {
    pub fn len(&self) -> usize {
        self.shape.iter().product()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The values as float64 (bool as 0 / 1, integers converted).
    pub fn to_f64(&self) -> Vec<f64> {
        match &self.data {
            Data::F64(v) => v.clone(),
            Data::Bool(v) => v.iter().map(|&b| if b { 1.0 } else { 0.0 }).collect(),
            Data::I64(v) => v.iter().map(|&x| x as f64).collect(),
            Data::I32(v) => v.iter().map(|&x| x as f64).collect(),
            Data::I16(v) => v.iter().map(|&x| x as f64).collect(),
            Data::U8(v) => v.iter().map(|&x| x as f64).collect(),
        }
    }

    pub fn f64(&self) -> Result<&[f64]> {
        match &self.data {
            Data::F64(v) => Ok(v),
            d => err!("an array of float64 expected, not {}", kind(d)),
        }
    }

    pub fn bools(&self) -> Result<&[bool]> {
        match &self.data {
            Data::Bool(v) => Ok(v),
            d => err!("an array of bool expected, not {}", kind(d)),
        }
    }

    /// Integers of any width as i64.
    pub fn ints(&self) -> Result<Vec<i64>> {
        match &self.data {
            Data::I64(v) => Ok(v.clone()),
            Data::I32(v) => Ok(v.iter().map(|&x| x as i64).collect()),
            Data::I16(v) => Ok(v.iter().map(|&x| x as i64).collect()),
            Data::U8(v) => Ok(v.iter().map(|&x| x as i64).collect()),
            d => err!("an array of integers expected, not {}", kind(d)),
        }
    }
}

fn kind(d: &Data) -> &'static str {
    match d {
        Data::F64(_) => "float64",
        Data::Bool(_) => "bool",
        Data::I64(_) => "int64",
        Data::I32(_) => "int32",
        Data::I16(_) => "int16",
        Data::U8(_) => "uint8",
    }
}

fn descr(d: &Data) -> &'static str {
    match d {
        Data::F64(_) => "<f8",
        Data::Bool(_) => "|b1",
        Data::I64(_) => "<i8",
        Data::I32(_) => "<i4",
        Data::I16(_) => "<i2",
        Data::U8(_) => "|u1",
    }
}

/// Reads one .npy file from its bytes.
pub fn parse(b: &[u8]) -> Result<Npy> {
    if b.len() < 10 || &b[..6] != MAGIC {
        return err!("not a .npy file");
    }
    let (hlen, start) = match b[6] {
        1 => (u16::from_le_bytes([b[8], b[9]]) as usize, 10),
        2 | 3 => {
            if b.len() < 12 {
                return err!("a truncated .npy header");
            }
            (u32::from_le_bytes([b[8], b[9], b[10], b[11]]) as usize, 12)
        }
        v => return err!(".npy format version {v} is not known"),
    };
    let end = start + hlen;
    if b.len() < end {
        return err!("a truncated .npy header");
    }
    let h = std::str::from_utf8(&b[start..end])
        .map_err(|_| crate::error::Error("a .npy header that is not text".into()))?;
    let descr = field(h, "descr")?;
    let descr = descr.trim_matches(|c| c == '\'' || c == '"');
    if field(h, "fortran_order")? != "False" {
        return err!("a .npy array in Fortran order (C order expected)");
    }
    let shape = parse_shape(field(h, "shape")?)?;
    let n: usize = shape.iter().product();
    let body = &b[end..];
    let need = |size: usize| -> Result<&[u8]> {
        if body.len() != n * size {
            return err!(
                ".npy data of {} bytes, {} expected for shape {:?}",
                body.len(),
                n * size,
                shape
            );
        }
        Ok(body)
    };
    let data = match descr {
        "<f8" => Data::F64(
            need(8)?
                .chunks_exact(8)
                .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                .collect(),
        ),
        "|b1" => Data::Bool(
            need(1)?
                .iter()
                .map(|&x| match x {
                    0 => Ok(false),
                    1 => Ok(true),
                    v => err!("a bool of value {v}"),
                })
                .collect::<Result<_>>()?,
        ),
        "<i8" => Data::I64(
            need(8)?
                .chunks_exact(8)
                .map(|c| i64::from_le_bytes(c.try_into().unwrap()))
                .collect(),
        ),
        "<i4" => Data::I32(
            need(4)?
                .chunks_exact(4)
                .map(|c| i32::from_le_bytes(c.try_into().unwrap()))
                .collect(),
        ),
        "<i2" => Data::I16(
            need(2)?
                .chunks_exact(2)
                .map(|c| i16::from_le_bytes(c.try_into().unwrap()))
                .collect(),
        ),
        "|u1" => Data::U8(need(1)?.to_vec()),
        d => return err!("the .npy type {d} is not one the engine reads (<f8, |b1, <i8, <i4, <i2, |u1)"),
    };
    Ok(Npy { shape, data })
}

/// The value of one key of the header dictionary, as text.
fn field<'a>(h: &'a str, key: &str) -> Result<&'a str> {
    let k = format!("'{key}':");
    let i = match h.find(&k) {
        Some(i) => i + k.len(),
        None => return err!(".npy header without {key}"),
    };
    let rest = h[i..].trim_start();
    let end = if rest.starts_with('(') {
        rest.find(')').map(|j| j + 1)
    } else {
        rest.find(',')
    };
    match end {
        Some(j) => Ok(rest[..j].trim()),
        None => err!(".npy header: no end of {key}"),
    }
}

fn parse_shape(s: &str) -> Result<Vec<usize>> {
    let inner = s.trim().trim_start_matches('(').trim_end_matches(')');
    inner
        .split(',')
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .map(|p| {
            p.parse::<usize>()
                .map_err(|_| crate::error::Error(format!("a .npy shape {s}")))
        })
        .collect()
}

/// Writes one array as .npy (format version 1.0, the header padded to 64 bytes as NumPy writes it).
pub fn write(shape: &[usize], data: &Data) -> Result<Vec<u8>> {
    let n: usize = shape.iter().product();
    let len = match data {
        Data::F64(v) => v.len(),
        Data::Bool(v) => v.len(),
        Data::I64(v) => v.len(),
        Data::I32(v) => v.len(),
        Data::I16(v) => v.len(),
        Data::U8(v) => v.len(),
    };
    if len != n {
        return err!("{len} values for shape {shape:?}");
    }
    let sh = match shape.len() {
        0 => "()".to_string(),
        1 => format!("({},)", shape[0]),
        _ => format!(
            "({})",
            shape.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(", ")
        ),
    };
    let mut h = format!(
        "{{'descr': '{}', 'fortran_order': False, 'shape': {}, }}",
        descr(data),
        sh
    );
    let total = 10 + h.len() + 1;
    h.push_str(&" ".repeat((64 - total % 64) % 64));
    h.push('\n');
    let mut out = Vec::with_capacity(10 + h.len() + n * 8);
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&[1, 0]);
    out.extend_from_slice(&(h.len() as u16).to_le_bytes());
    out.extend_from_slice(h.as_bytes());
    match data {
        Data::F64(v) => v.iter().for_each(|x| out.extend_from_slice(&x.to_le_bytes())),
        Data::Bool(v) => v.iter().for_each(|&x| out.push(x as u8)),
        Data::I64(v) => v.iter().for_each(|x| out.extend_from_slice(&x.to_le_bytes())),
        Data::I32(v) => v.iter().for_each(|x| out.extend_from_slice(&x.to_le_bytes())),
        Data::I16(v) => v.iter().for_each(|x| out.extend_from_slice(&x.to_le_bytes())),
        Data::U8(v) => out.extend_from_slice(v),
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_keeps_every_bit() {
        let v = vec![1.0, -0.0, f64::NAN, f64::INFINITY, 0.1 + 0.2, f64::MIN_POSITIVE / 3.0];
        for (shape, data) in [
            (vec![2, 3], Data::F64(v.clone())),
            (vec![6], Data::Bool(vec![true, false, true, true, false, false])),
            (vec![3, 2], Data::I64(vec![-1, 0, 1, i64::MAX, i64::MIN, 7])),
            (vec![0, 4], Data::F64(vec![])),
            (vec![], Data::I32(vec![-5])),
        ] {
            let back = parse(&write(&shape, &data).unwrap()).unwrap();
            assert_eq!(back.shape, shape);
            match (&back.data, &data) {
                (Data::F64(a), Data::F64(b)) => {
                    assert!(a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits()))
                }
                (a, b) => assert_eq!(a, b),
            }
        }
    }

    #[test]
    fn refuses_a_short_body_and_an_unknown_type() {
        let mut b = write(&[3], &Data::F64(vec![1.0, 2.0, 3.0])).unwrap();
        b.pop();
        assert!(parse(&b).is_err());
        let mut b = write(&[1], &Data::F64(vec![1.0])).unwrap();
        let i = b.windows(3).position(|w| w == b"<f8").unwrap();
        b[i..i + 3].copy_from_slice(b"<f4");
        assert_eq!(
            parse(&b).unwrap_err().0,
            "the .npy type <f4 is not one the engine reads (<f8, |b1, <i8, <i4, <i2, |u1)"
        );
    }
}
