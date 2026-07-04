# PLC register map — the contract between plant-sim, program.st, and (Phase-2F.2) the tag server

One source of truth. If an address changes here, it changes in `plant/sim/main.py` (`plc_loop`),
`plc/program.st`, and later the tag server's tag table — nowhere else.

## Modbus addressing (OpenPLC slave)

OpenPLC maps IEC locations onto Modbus like this: coils `%QX0.0..` at address 0+, and the
`%MW0..` memory words inside the HOLDING register space at offset **1024** (`PLC_MW_BASE`,
env-overridable on the sim — **verify once on the box** against the OpenPLC address-mapping
docs; if writes land nowhere, this offset is the first suspect).

The sim is the Modbus **client** (master): FC16 writes the sensor words, FC01 reads the coils.
Poll cadence = sim tick (1 s); PLC scan = 100 ms.

## Holding registers (sim → PLC), `%MW<n>` = holding address 1024+n

| %MW | Meaning | Scaling | ST variable |
|---|---|---|---|
| 0 | press-1 coolant-side temp | °C ×10 | `t_press1` |
| 1 | press-2 temp | °C ×10 | `t_press2` |
| 2 | cnc-1 temp | °C ×10 | `t_cnc1` |
| 3 | furnace-1 temp | °C ×10 | `t_furnace1` |
| 4 | coolant flow | L/min ×10 | (tag server only) |
| 5 | pump health | ratio ×100 | (tag server only) |
| 6 | rail psu-a voltage | V ×10 | (tag server only) |
| 7 | rail psu-b voltage | V ×10 | (tag server only) |
| 8–15 | per-machine current draw, DEVICES order: press-1, press-2, cnc-1, qa-scanner-1, conveyor-1, compressor-1, furnace-1, chiller-1 | A ×10 | (tag server only) |
| 20 | operator reset request (write 1; program consumes) | bool-ish | `reset_cmd` |

## Coils (PLC → sim), address 0+

| Coil | Meaning | ST variable |
|---|---|---|
| 0 | trip press-1 (contactor open) | `trip_press1` |
| 1 | trip press-2 | `trip_press2` |
| 2 | trip cnc-1 | `trip_cnc1` |
| 3 | trip furnace-1 | `trip_furnace1` |

## Semantics

- **Trips are latched** in the PLC and clear only on `reset_cmd` **and** temp < 78.0 °C — the
  interlock discipline. The sim's `POST /reset` pulses `reset_cmd`.
- **PLC unreachable → the sim fails OPEN** (machines keep running, open-loop) so the demo
  survives; a production safety PLC would fail SAFE. Stated honestly on camera if asked.
- Registers 4–15 are exposed for the Phase-2F.2 tag server (ISA-88 tag names live there),
  not consumed by the trip program.
