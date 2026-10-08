"""plant.model: the physics library of the plant sim overhaul (ideas.md sections 11.2 and 11.3).

Each module is one shared part model or one machine. A model takes its physical inputs (supply
voltage, cooling water, air, PLC commands) and gives the signals a real machine gives. No model
reads another model's state directly: the plant wires them together.

Every number comes from a cited source or carries the mark "project choice". plant/model/README.md
lists the sources and the verification result of each model.
"""
