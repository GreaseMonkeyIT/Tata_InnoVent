#!/usr/bin/env python3
"""plant-sim: a physics-first plant emulator (Stage-2 pivot, LOG-027/029).

PHYSICS, NOT SCRIPTS. Faults perturb a lumped dynamical model; symptoms EMERGE:
  - Electrical: each rail has a source voltage and source impedance. Rail voltage
    V = V_src - I_total * R_src. A device drawing more current sags the rail for
    EVERYONE on it. Nothing ever writes "voltage low" directly.
  - Thermal: a shared coolant loop with a pump curve. Each cooled machine is a
    first-order thermal system: T' = (T_amb + k_heat*P_heat/flow_share - T)/tau.
    Pump degradation lowers flow -> every machine on the loop warms, each with
    its own time constant -> REAL lag structure for the correlation engine.

Serves:
  GET  /metrics        Prometheus exposition; every series labeled
                       {namespace="plant", pod="<asset>"} so the existing L2
                       aggregator ingests it with zero code changes.
  POST /fault/<id>     PS-series fault injection (PS1, PS2, PS5). GET lists.
  POST /reset          clear all faults
  GET  /healthz        liveness
  GET  /state          debug JSON of the whole world

Honesty label carried by every metric name: plant_* = physics-SIMULATED plant.
The inference downstream is real; the substrate is a model and says so.

2F (LOG-033): the sim doubles as the FIELD WIRING for a real OpenPLC runtime — sensor words
out over Modbus TCP, trip coils back in (a tripped machine's contactor opens). Set PLC_HOST
to enable; without it (or without pymodbus) the sim runs exactly as before, open-loop.
"""
import json
import math
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

TICK_S = float(os.environ.get("TICK_S", "1.0"))
PORT = int(os.environ.get("PORT", "9200"))
NS = os.environ.get("PLANT_NS", "plant")

# ---- PLC loop (2F, LOG-033): real Modbus TCP to OpenPLC; OPEN-LOOP if absent. ----
# The sim is the "field wiring": every tick it writes the sensor words into the PLC's %MW
# holding area and reads the trip coils back; a tripped machine's contactor opens (duty -> 0).
# pymodbus is the ONE non-stdlib dep, and it is optional — import failure or PLC_HOST=""
# keeps the sim fully functional open-loop (the demo must survive the PLC being down).
PLC_HOST = os.environ.get("PLC_HOST", "")            # e.g. openplc.plant.svc; empty = open-loop
PLC_PORT = int(os.environ.get("PLC_PORT", "502"))
PLC_MW_BASE = int(os.environ.get("PLC_MW_BASE", "1024"))  # %MW0 in OpenPLC's holding space (see plc/REGISTER_MAP.md)
try:
    from pymodbus.client import ModbusTcpClient      # pymodbus 3.x
except Exception:
    ModbusTcpClient = None
PLC_STATE = {"connected": False, "mode": "open-loop" if not PLC_HOST else "connecting"}
_PLC_RESET = threading.Event()                        # /reset pulses the PLC's reset word

# ---------------------------------------------------------------- the world --
class Rail:
    """A shared DC bus: V = v_src - i_total * r_src. The coupling medium."""
    def __init__(self, name, v_src=400.0, r_src=0.35):
        self.name, self.v_src, self.r_src = name, v_src, r_src
        self.voltage = v_src

    def step(self, i_total):
        # small source noise so baselines learn a live band, not a constant
        self.voltage = self.v_src - i_total * self.r_src + random.gauss(0, 0.15)


class CoolantLoop:
    """A shared loop: pump provides flow; machines take shares and dump heat.
    Degraded pump -> less flow -> everyone's temperature rises (lagged)."""
    def __init__(self, name, flow_nominal=120.0):
        self.name, self.flow_nominal = name, flow_nominal
        self.pump_health = 1.0            # 1.0 healthy .. 0.3 badly degraded
        self.flow = flow_nominal

    def step(self):
        self.flow = max(self.flow_nominal * self.pump_health + random.gauss(0, 0.8), 5.0)


class Device:
    """One asset. Draws current from its rail; optionally cooled by the loop.
    duty(t) in [0,1] scales electrical load. Faults multiply friction/heat."""
    def __init__(self, name, rail, i_base, loop=None, tau=45.0, heat_k=0.55,
                 duty=None, v_sensitive=False):
        # heat_k calibration: steady temps must sit ~50-65C with healthy flow so the PS5
        # story works (pump degrades -> share drops -> temps CROSS TRIP_C=78, not live
        # above it). At heat_k=0.55: press-1 steady = 35 + 0.55*42 = 58C; under PS5
        # (share 0.45) = 35 + 0.55*42/0.45 = 86C -> trips. The old 6.0 put steady at
        # ~287C, permanently past trip - the forecast target was unreachable.
        self.name, self.rail, self.loop = name, rail, loop
        self.i_base = i_base              # amps at duty=1, healthy
        self.tau = tau                    # thermal time constant (s) -> REAL lags
        self.heat_k = heat_k
        self.duty_fn = duty or (lambda t: 1.0)
        self.v_sensitive = v_sensitive    # degrades visibly under rail sag
        self.friction = 1.0               # PS1 fault raises this
        self.temp = 35.0
        self.current = 0.0
        self.throughput = 100.0           # % of nominal work rate
        self.tripped = False              # PLC trip coil (2F): contactor open

    def step(self, t, dt):
        if self.tripped:
            # contactor open: no drive current (rail load drops -> voltage RECOVERS, physics),
            # work stops, and the machine cools toward ambient with its own lag
            self.current = max(random.gauss(0.2, 0.02), 0.0)
            self.throughput = max(0.0, self.throughput - 5.0)
            if self.loop is not None:
                self.temp += (35.0 - self.temp) * (dt / self.tau) + random.gauss(0, 0.05)
            return
        duty = self.duty_fn(t)
        # electrical load: friction directly raises current draw (the PHYSICS
        # of a binding bearing: more torque -> more amps). Never scripted.
        self.current = self.i_base * duty * self.friction + random.gauss(0, 0.05)
        # brownout physics: under-voltage raises current a little (constant
        # power) and cuts throughput for sensitive devices.
        v = self.rail.voltage
        if v < 0.92 * self.rail.v_src:
            self.current *= min(1.15, (0.92 * self.rail.v_src) / max(v, 1.0))
            if self.v_sensitive:
                self.throughput = max(20.0, 100.0 * v / self.rail.v_src - random.uniform(0, 3))
        else:
            self.throughput = min(100.0, self.throughput + 2.0)
        # thermal: first-order response to heat load over the flow share
        if self.loop is not None:
            heat = self.heat_k * self.current
            share = max(self.loop.flow / self.loop.flow_nominal, 0.05)
            t_target = 35.0 + heat / share
            self.temp += (t_target - self.temp) * (dt / self.tau) + random.gauss(0, 0.05)


# ------------------------------------------------------------- build the plant
RAIL_A = Rail("psu-a")
RAIL_B = Rail("psu-b")
LOOP = CoolantLoop("cool-1")

def compressor_duty(t):
    # PS2's aggressor personality: OFF most of the time, heavy when the header
    # "calls" - a 300s cycle with a 60s high-draw window (no matured baseline).
    return 1.0 if (t % 300) < 60 else 0.12

DEVICES = [
    Device("press-1",      RAIL_A, i_base=42.0, loop=LOOP, tau=40.0),
    Device("press-2",      RAIL_A, i_base=38.0, loop=LOOP, tau=55.0),
    Device("cnc-1",        RAIL_A, i_base=25.0, loop=LOOP, tau=30.0, v_sensitive=True),
    Device("qa-scanner-1", RAIL_A, i_base=6.0,  v_sensitive=True),
    Device("conveyor-1",   RAIL_B, i_base=18.0),
    Device("compressor-1", RAIL_B, i_base=55.0, duty=compressor_duty),
    Device("furnace-1",    RAIL_B, i_base=30.0, loop=LOOP, tau=90.0, heat_k=1.0),   # runs hot: steady 65C, PS5 -> ~102C
    Device("chiller-1",    RAIL_B, i_base=22.0),   # drives LOOP.pump_health
]
BY_NAME = {d.name: d for d in DEVICES}

# PS-series faults: each perturbs the MODEL; the chain emerges. (LOG-028 map.)
FAULTS = {
    "PS1": {"desc": "press-1 bearing friction rises -> rail-A sag cascade",
            "apply": lambda: setattr(BY_NAME["press-1"], "friction", 1.9),
            "clear": lambda: setattr(BY_NAME["press-1"], "friction", 1.0)},
    "PS2": {"desc": "compressor stuck-on: continuous heavy draw on rail B",
            "apply": lambda: setattr(BY_NAME["compressor-1"], "duty_fn", lambda t: 1.0),
            "clear": lambda: setattr(BY_NAME["compressor-1"], "duty_fn", compressor_duty)},
    "PS5": {"desc": "chiller pump degrades -> coolant flow drops -> temps ramp to trip",
            "apply": lambda: setattr(LOOP, "pump_health", 0.45),
            "clear": lambda: setattr(LOOP, "pump_health", 1.0)},
}
ACTIVE = set()
TRIP_C = 78.0     # the PS5 forecast target: coolant-side trip threshold

# ------------------------------------------------------------------ sim loop --
_lock = threading.Lock()
T0 = time.time()

# Register/coil order is the contract with plc/program.st — see plc/REGISTER_MAP.md.
COOLED = ["press-1", "press-2", "cnc-1", "furnace-1"]


def plc_loop():
    """The field wiring (2F): every tick, write the sensor words into the PLC's %MW holding
    area (FC16) and read the trip coils back (FC01); a set coil opens that machine's contactor.
    PLC unreachable -> fail OPEN for the demo (machines keep running, sim goes open-loop and
    reconnects forever) — documented in PIVOT_SETUP; a real safety PLC would fail SAFE."""
    if ModbusTcpClient is None or not PLC_HOST:
        return
    client = None
    while True:
        try:
            if client is None:
                client = ModbusTcpClient(PLC_HOST, port=PLC_PORT, timeout=2)
            if not client.connected and not client.connect():
                raise ConnectionError(f"no route to PLC {PLC_HOST}:{PLC_PORT}")
            with _lock:
                regs = [max(0, int(BY_NAME[n].temp * 10)) for n in COOLED]        # MW0..3 temp x10
                regs += [max(0, int(LOOP.flow * 10)), max(0, int(LOOP.pump_health * 100)),
                         max(0, int(RAIL_A.voltage * 10)), max(0, int(RAIL_B.voltage * 10))]  # MW4..7
                regs += [max(0, int(d.current * 10)) for d in DEVICES]             # MW8..15 amps x10
            client.write_registers(PLC_MW_BASE, regs, slave=1)
            if _PLC_RESET.is_set():
                client.write_registers(PLC_MW_BASE + 20, [1], slave=1)            # MW20: reset cmd
                _PLC_RESET.clear()
            rr = client.read_coils(0, count=len(COOLED), slave=1)                 # QX0.0..0.3 trips
            if not rr.isError():
                bits = list(rr.bits)[:len(COOLED)]
                with _lock:
                    for name, b in zip(COOLED, bits):
                        BY_NAME[name].tripped = bool(b)
                    PLC_STATE.update(connected=True, mode="closed-loop")
        except Exception:
            with _lock:
                PLC_STATE.update(connected=False, mode="reconnecting")
                for name in COOLED:
                    BY_NAME[name].tripped = False
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            client = None
            time.sleep(5.0)
        time.sleep(TICK_S)

def loop():
    last = time.time()
    while True:
        now = time.time()
        dt, last = max(now - last, 1e-3), now
        t = now - T0
        with _lock:
            for rail in (RAIL_A, RAIL_B):
                i_total = sum(d.current for d in DEVICES if d.rail is rail)
                rail.step(i_total)
            LOOP.step()
            for d in DEVICES:
                d.step(t, dt)
        time.sleep(max(0.0, TICK_S - (time.time() - now)))

# ----------------------------------------------------------------- exposition --
def metrics_text():
    """Prometheus exposition. Labels namespace/pod match what the L2 aggregator
    already extracts from every query result - zero aggregator changes."""
    L = []
    def g(metric, pod, val, extra=""):
        L.append(f'{metric}{{namespace="{NS}",pod="{pod}"{extra}}} {val:.4f}')
    with _lock:
        for r in (RAIL_A, RAIL_B):
            g("plant_bus_voltage_volts", r.name, r.voltage)
        g("plant_coolant_flow_lpm", LOOP.name, LOOP.flow)
        g("plant_pump_health_ratio", LOOP.name, LOOP.pump_health)
        for d in DEVICES:
            g("plant_current_draw_amps", d.name, d.current)
            g("plant_throughput_pct", d.name, d.throughput)
            # every device also reports the voltage IT sees (its rail's) - the
            # per-victim sag signal the engine correlates against the source's amps
            g("plant_bus_voltage_volts", d.name, d.rail.voltage)
            if d.loop is not None:
                g("plant_temp_celsius", d.name, d.temp)
                g("plant_heat_load_watts", d.name, d.heat_k * d.current)
        g("plant_trip_threshold_celsius", "cool-1", TRIP_C)
        for n in COOLED:                                  # PLC trip coils, mirrored per machine
            g("plant_trip_active", n, 1.0 if BY_NAME[n].tripped else 0.0)
        L.append(f'plant_plc_connected{{namespace="{NS}",pod="plant-sim"}} '
                 f'{1 if PLC_STATE["connected"] else 0}')
        for fid in FAULTS:
            L.append(f'plant_fault_active{{namespace="{NS}",pod="plant-sim",fault="{fid}"}} '
                     f'{1 if fid in ACTIVE else 0}')
    return "\n".join(L) + "\n"

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path == "/metrics":
            return self._send(200, metrics_text())
        if self.path == "/healthz":
            return self._send(200, "ok\n")
        if self.path.startswith("/fault"):
            return self._send(200, json.dumps(
                {fid: {"desc": f["desc"], "active": fid in ACTIVE} for fid, f in FAULTS.items()},
                indent=2), "application/json")
        if self.path == "/state":
            # carries the static topology too (rail/cooled/nominals) so the dashboard's
            # Machines section can group by medium without a second source of truth
            with _lock:
                return self._send(200, json.dumps({
                    "rails": {r.name: {"volts": round(r.voltage, 2), "v_src": r.v_src}
                              for r in (RAIL_A, RAIL_B)},
                    "loop": {"name": LOOP.name, "flow": round(LOOP.flow, 1),
                             "flow_nominal": LOOP.flow_nominal,
                             "pump_health": round(LOOP.pump_health, 2)},
                    "trip_c": TRIP_C,
                    "devices": {d.name: {"amps": round(d.current, 2),
                                         "temp": round(d.temp, 1) if d.loop else None,
                                         "throughput": round(d.throughput, 1),
                                         "rail": d.rail.name,
                                         "cooled": d.loop is not None,
                                         "tripped": d.tripped} for d in DEVICES},
                    "plc": dict(PLC_STATE),
                    "active_faults": sorted(ACTIVE)}, indent=2), "application/json")
        self._send(404, "not found\n")

    def do_POST(self):
        if self.path == "/reset":
            with _lock:
                for fid in list(ACTIVE):
                    FAULTS[fid]["clear"]()
                ACTIVE.clear()
            _PLC_RESET.set()     # pulse the PLC's reset word; latched trips clear only if the
            return self._send(200, "all faults cleared; PLC trip reset requested\n")  # condition is gone
        if self.path.startswith("/fault/"):
            fid = self.path.rsplit("/", 1)[-1].upper()
            if fid not in FAULTS:
                return self._send(404, f"unknown fault {fid}; have {sorted(FAULTS)}\n")
            with _lock:
                FAULTS[fid]["apply"]()
                ACTIVE.add(fid)
            return self._send(200, f"{fid} injected: {FAULTS[fid]['desc']}\n")
        self._send(404, "not found\n")

    def log_message(self, *_):
        pass

def main():
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=plc_loop, daemon=True).start()
    print(f"plant-sim up on :{PORT} | {len(DEVICES)} devices, rails A/B, loop cool-1 | "
          f"faults: {', '.join(sorted(FAULTS))} | plc: {PLC_STATE['mode']}"
          f"{' @ ' + PLC_HOST + ':' + str(PLC_PORT) if PLC_HOST else ''}", flush=True)
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()

if __name__ == "__main__":
    main()
