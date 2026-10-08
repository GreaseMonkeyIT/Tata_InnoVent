"""twin: the learned plant model of the engine overhaul (ideas.md section 12).

A node's small model predicts the node's signals from its inputs (supply voltage, cooling water,
PLC and controller states, the signals of the nodes that feed it) and their recent history. The
residual (measured minus predicted) is what the engine's statistics read (ideas.md 12.3, steps 3
to 5). The twin never reads the sim's code: it learns from signals only.
"""
