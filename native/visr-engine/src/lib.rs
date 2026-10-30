//! The VISR engine (native/README.md): one native program for the node models, the incidents, the
//! twin and the root of a fault, on the readings of the plant. Python is the reference: each part
//! gives the numbers of its Python part (live/engine.py, eval/nodes.py, twin/, eval/cf.py), and a
//! test proves it (tests/goldens.rs). The engine never imports Python.
//!
//! Step N1: the model pack, one node one sample at a time, and the node's portable state.

pub mod error;
pub mod isa;
pub mod neutral;
pub mod node;
pub mod npy;
pub mod pack;
pub mod sample;
pub mod settings;
pub mod state;

pub use error::{Error, Result};
pub use isa::{Choice, Kernels, Path};
pub use node::Node;
pub use pack::{NodePack, Pack};
pub use sample::Value;
pub use state::NodeState;
