# Register maps: one YAML file per PLC vendor or model

A register map tells the SCADA driver where a PLC keeps each value. Adding a new vendor,
or a PLC whose program uses different addresses, is a new file here. No code changes.

## Use one
Put `<name>.yaml` in this folder (or in `REGMAP_DIR`, e.g. a ConfigMap mount) and enrol the
PLC with `"protocol": {"kind": "modbus", "host": "...", "map": "<name>"}`. An enrolment with
no `map` uses the built-in VISR layout (FLEET.md 4.2 / 4.3), exactly as before.
A bad map is refused at enrolment with HTTP 400 and a list of every bad field.
Edited a map? Restart the tag server (`kubectl -n plant rollout restart deploy/tag-server`).

## File format
| Key | Where | Meaning |
|---|---|---|
| vendor, model, notes | top | documentation (vendor is required) |
| protocol | top | `modbus` or `s7comm` |
| defaults | top | any point field, applied to every point unless the point sets it |
| read.max_gap | top | merge reads across up to N unmapped units (default 0, the safe choice) |
| points | top | the list below |
| slot | point | VISR image slot: `%IX0.0`..`%IX7.7`, `%QX..`, `%IW0`..`%IW63`, `%QW..`, `%MW..` |
| signal | point | documentation, e.g. `AMPS` |
| area | point | Modbus: `discrete_input`, `coil`, `input_register`, `holding_register`. S7: `db`, `input`, `output`, `marker` |
| address, base | Modbus | the address as your manual prints it, and whether that manual counts from 0 or 1 |
| db, offset, bit | S7 | data block (area db only), byte offset, bit 0..7 (BOOL only) |
| type | point | `BOOL`, `INT16`, `UINT16`, `INT32`, `UINT32`, `FLOAT32` |
| word_order | point | 32-bit types: `big` (ABCD, high word first) or `little` (CDAB) |
| scale | point | image value = PLC value x scale (writes divide by it) |
| direction | point | `read` (default), `write`, `read_write`. Writes only on `%MW` slots and writable areas |

The slot is what keeps everything downstream unchanged: the tag table (fleet.py) reads slots,
so `FLEET.<PLC>.<MACHINE>.AMPS` is the same tag whatever the vendor.

## Limits (be honest about these)
- A slot the map does not list reads as 0. Map every slot your PLC's tag table uses.
- Image slots are 16-bit INT. A value that does not fit after scaling is clamped, and the
  driver lists that slot in `last_clamped`. Pick a scale that keeps values in -32768..32767.
- Byte-swapped 32-bit orders (BADC, DCBA) are not supported yet.
- Siemens S7-1200/1500: enable PUT/GET and turn optimized block access off for mapped DBs.
- These maps are tested against protocol-level fakes, not yet against physical PLCs.