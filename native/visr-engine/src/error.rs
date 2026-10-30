//! One error type for the engine: a message that says what failed and where.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);

pub type Result<T> = std::result::Result<T, Error>;

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error(e.to_string())
    }
}

impl From<serde_json::Error> for Error {
    fn from(e: serde_json::Error) -> Self {
        Error(format!("json: {e}"))
    }
}

/// `err!("...", args)`: an `Err(Error)` with a formatted message.
#[macro_export]
macro_rules! err {
    ($($t:tt)*) => { Err($crate::error::Error(format!($($t)*))) };
}

/// Adds where an error happened: `.ctx(|| format!("pack {dir}"))`.
pub trait Context<T> {
    fn ctx<F: FnOnce() -> String>(self, f: F) -> Result<T>;
}

impl<T, E: Into<Error>> Context<T> for std::result::Result<T, E> {
    fn ctx<F: FnOnce() -> String>(self, f: F) -> Result<T> {
        self.map_err(|e| Error(format!("{}: {}", f(), e.into().0)))
    }
}
