//! One reading of one tag, as the plant service gives it: a number, a text state, or nothing.

#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    Num(f64),
    Text(String),
    Missing,
}

pub const MISSING: Value = Value::Missing;

impl Value {
    /// The number of a numeric input (live/engine.py `_num`): NaN for a text or a missing value.
    #[inline]
    pub fn num(&self) -> f64 {
        match self {
            Value::Num(v) => *v,
            _ => f64::NAN,
        }
    }

    /// True when the value is the text state c (a number is never a state).
    #[inline]
    pub fn is(&self, c: &str) -> bool {
        matches!(self, Value::Text(s) if s == c)
    }

    pub fn text(&self) -> Option<&str> {
        match self {
            Value::Text(s) => Some(s),
            _ => None,
        }
    }
}
