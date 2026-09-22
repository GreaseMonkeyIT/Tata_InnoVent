#!/usr/bin/env python3
"""plant-sim: a physics-first plant emulator (Stage-2 pivot, LOG-027/029).

PHYSICS, NOT SCRIPTS. Faults perturb a lumped dynamical model; symptoms EMERGE:
  - Electrical: one supply (the incoming board) feeds every rail. Each rail has a
    source impedance. Rail voltage V = V_supply - I_total * R_src. A device drawing
    more current sags the rail for EVERYONE on it. A supply dip sags EVERY rail at
    once, with no machine leading it. Nothing ever writes "voltage low" directly.
  - Thermal: a shared coolant loop with a pump curve. Each cooled machine is a
    first-order thermal system: T' = (T_amb + k_heat*P_heat/flow_share - T)/tau.
    Pump degradation lowers flow -> every machine on the loop warms, each with
    its own time constant -> REAL lag structure for the correlation engine.

Serves:
  GET    /metrics        Prometheus exposition; every series labeled
                         {namespace="plant", pod="<asset>"} so the existing L2
                         aggregator ingests it with zero code changes.
  POST   /fault/<id>     PS-series fault injection (PS1, PS2, PS3, PS4B, PS5, PS7). GET lists.
                         A repeat POST of an active fault changes nothing (inject_fault).
  POST   /reset          clear all faults, relay trips, and replays
  GET    /healthz        liveness
  GET    /state          debug JSON of the whole world, cells and segments included
  GET    /cells          every cell: its PLC, rail, connection, mode, and machines
  POST   /cells          add a cell of new machines wired to a virtual PLC (FLEET.md 7)
  DELETE /cells/<cell>   remove a cell and its machines. Base cells are refused (403).
  GET    /domains        live supply, rail, loop, and network memberships for the engine (FLEET.md 7 and 9)

Honesty label carried by every metric name: plant_* = physics-SIMULATED plant.
The inference downstream is real; the substrate is a model and says so.

2F (LOG-033): the sim doubles as the FIELD WIRING for a real OpenPLC runtime — sensor words
out over Modbus TCP, trip coils back in (a tripped machine's contactor opens). Set PLC_HOST
to enable; without it (or without pymodbus) the sim runs exactly as before, open-loop.

2H (FLEET.md section 7): cells. A cell is a group of machines that one virtual PLC commands
through its field port (Modbus TCP, FLEET.md 4.1). The sim writes each machine's AMPS, TEMP,
VOLTS, THROUGHPUT, and READY to the PLC. It reads RUN and SPEED_PCT back. The PLC commands the
machines. The physics stays here. The 8 base devices keep their fixed order in BASE_DEVICES,
because the OpenPLC register map depends on it. Cell machines live in CELL_DEVICES.
  - BASE_CELLS binds base machines to a PLC. Format: cell|host:port|failopen|machine,machine;...
    The default binds the stamping cell (press-1, press-2) to plc-stamping, fail-open.
  - Speed model: speed_frac = SPEED_PCT/100 while RUN, 0.03 when not RUN. A fail-open cell
    without a PLC connection runs at 1.0, so the base plant behaves as before. A new cell
    without a PLC stays idle. The drive ramp moves the speed at most 10 points per second.
  - Current = i_base * duty(t) * speed_frac * friction. Throughput = 100 * speed_frac while
    RUN, else 0. A V-sensitive machine keeps its brownout derate on top of that.
  - /metrics adds plant_commanded_speed_pct{pod=<machine>} while a PLC commands the machine
    (SPEED_PCT when RUN, 0 when not RUN) and plant_cell_connected{pod=<cell>}.

Scenario set (SCENARIOS.md sections 2 and 3). Each fault perturbs the model. The symptoms emerge.
  - PS2: chiller-1 has a motor overload relay (class Overload). It uses no random numbers. The
    relay heats above 1.02 x rated current and trips at heat 90. The trip latches until /reset.
    A tripped chiller cuts the loop flow to CHILLER_RESIDUAL_FLOW of nominal. A normal compressor
    window never trips it. A stuck-on compressor keeps rail B low, and the relay trips.
  - PS3: a field network segment (class Segment, an M/M/1/K queue model). FIELD_SEGMENTS sets
    it: name|talkers|capacity_fps|buffer. Every cell PLC link runs through the first segment.
    The queue is a model. Its delays and drops act on the real Modbus requests of each cell.
    The OpenPLC link stays off the segment. The fault ramps the talker hmi-gw to 1500 frames/s.
  - PS4B: press-1 friction rises to 1.4, and its vPLC AMPS word replays the last 30 s of real
    current. The OpenPLC word MW8 and every plant_* metric keep the truth.
  - Feeder meter: /state rails.<name>.amps and plant_feeder_current_amps{pod=<rail>} sum the
    device currents on each rail at export time.
"""
import json
import math
import os
import random
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TICK_S = float(os.environ.get("TICK_S", "1.0"))
MAX_DT_S = 5.0            # the longest physics step after a stall (tick_dt). Keep it under MIN_TAU_S.
MIN_TAU_S = 10.0          # the shortest thermal time constant a cell machine may have
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

# ---- cells (2H, FLEET.md section 7): virtual PLCs command groups of machines. ----
DEFAULT_BASE_CELLS = "stamping|plc-stamping.fleet.svc.cluster.local:5020|failopen|press-1,press-2"
BASE_CELLS = os.environ.get("BASE_CELLS", DEFAULT_BASE_CELLS)
DEFAULT_FIELD_PORT = 5020
CELL_RETRY_S = float(os.environ.get("CELL_RETRY_S", "5.0"))      # wait after a failed sync
CELL_TIMEOUT_S = float(os.environ.get("CELL_TIMEOUT_S", "2.0"))  # Modbus request timeout
CELL_SLOTS = 8            # machine index k = 0..7 in the wiring convention (FLEET.md 3.1)
IDLE_SPEED_FRAC = 0.03    # drive speed when the PLC does not command RUN
RAMP_PER_S = 0.10         # drive ramp: at most 10 percentage points per second
MAX_BODY = 64 * 1024
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,30}[a-z0-9])?$")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9.]{0,251}[A-Za-z0-9])?$")
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# ---- PS2 (SCENARIOS.md 2.2): the chiller-1 overload relay and the loop it drives. ----
CHILLER_OL_PICKUP = float(os.environ.get("CHILLER_OL_PICKUP", "1.02"))        # x rated current
CHILLER_OL_UP_PER_S = float(os.environ.get("CHILLER_OL_UP_PER_S", "1.0"))     # heat gain above pickup
CHILLER_OL_DOWN_PER_S = float(os.environ.get("CHILLER_OL_DOWN_PER_S", "0.5")) # heat loss below pickup
CHILLER_OL_TRIP = float(os.environ.get("CHILLER_OL_TRIP", "90"))              # trip level, latched
CHILLER_RESIDUAL_FLOW = float(os.environ.get("CHILLER_RESIDUAL_FLOW", "0.60"))  # flow share, chiller off
# 0.45 -> 0.60 on 2026-09-20 (LOG-073). At 0.45 the loop fell to 54 L/min, three cooled machines
# reached the 78 C latch, and their contactors opened. That unloaded rail B, the sag went away, and
# the rail hop died before the loop hop was established, which is the PS2 failure. Measured in
# correlation/tests/ps2_lab.py: the window where the root is compressor-1 AND the loop hop is up
# grows from 120 s to 180 s, and the trips fall from three machines to one (furnace-1). Above 0.70
# nothing trips at all, so the trip forecast would predict an event that never arrives.

# ---- PS7 (SCENARIOS.md 2.8): the supply above every rail. A disturbance here reaches every
#      rail at once, from above the plant load. That is what separates an external cause from
#      an internal one. ----
SUPPLY_NAME = os.environ.get("SUPPLY_NAME", "incomer-1")
SUPPLY_NOMINAL_V = float(os.environ.get("SUPPLY_NOMINAL_V", "400.0"))
SUPPLY_RAMP_S = float(os.environ.get("SUPPLY_RAMP_S", "3.0"))      # full-scale slew time (Supply)
SUPPLY_DIP_PCT = float(os.environ.get("SUPPLY_DIP_PCT", "0.85"))   # PS7 target, share of nominal

# ---- PS3 (SCENARIOS.md 2.3): the field network segment. ----
DEFAULT_FIELD_SEGMENTS = "field-1|hmi-gw|1000|50"
FIELD_SEGMENTS = os.environ.get("FIELD_SEGMENTS", DEFAULT_FIELD_SEGMENTS)   # empty disables it
NET_RTO_S = 0.2           # first retransmit timeout. It doubles after each loss.
NET_TALKER_FPS = 20.0     # a talker's offered load at rest, frames/s
NET_TALKER_NOISE = 0.03   # relative noise on a talker's load, drawn once per second
NET_STORM_FPS = 1500.0    # PS3: the talker's load at the end of the ramp
NET_RAMP_S = 45.0         # PS3: the ramp time
NET_WINDOW_S = 5.0        # a PLC member's offered load counts its frames over this window

# ---- PS4B (SCENARIOS.md 2.5): press-1 friction and a replayed AMPS word. ----
PS4B_FRICTION = 1.4       # about 61 A on press-1, below its thermal trip
PS4B_REPLAY_S = 30.0      # the recording that the vPLC AMPS word replays
REPLAY_N = max(1, int(round(PS4B_REPLAY_S / TICK_S)))   # physics ticks in the recording

# ---------------------------------------------------------------- the world --
class Supply:
    """The incoming board above every rail (SCENARIOS.md 2.8). It is the only place in the model
    that can hold an external cause.

    `target` is a share of nominal. The voltage slews toward it at v_nom / ramp_s volts per second
    (133 V/s by default). It never jumps inside a tick, but the 15 % PS7 dip lands within one 1 s
    tick. This class draws NO random numbers, so at target 1.0 the rail stream is bit-identical
    to the pre-supply model."""
    def __init__(self, name=None, v_nom=None, ramp_s=None):
        self.name = SUPPLY_NAME if name is None else name
        self.v_nom = SUPPLY_NOMINAL_V if v_nom is None else v_nom
        self.ramp_s = SUPPLY_RAMP_S if ramp_s is None else ramp_s
        self.target = 1.0          # share of nominal; PS7 lowers it
        self.voltage = self.v_nom
        self.amps = 0.0            # plant total current, the source signal of rail:<supply>

    def step(self, i_total, dt):
        want = self.v_nom * self.target
        limit = (self.v_nom / max(self.ramp_s, 1e-3)) * dt
        gap = want - self.voltage
        self.voltage += gap if abs(gap) <= limit else (limit if gap > 0 else -limit)
        self.amps = i_total

    def dipped(self):
        return self.target < 1.0


class Rail:
    """A shared DC bus: V = supply.voltage - i_total * r_src. The coupling medium.

    `v_nom` is the fixed RATING. Every threshold, band, and ratio uses `v_nom`, never the live
    supply. A motor does not re-rate itself when the grid sags, and a threshold that tracks the
    dip makes the brownout branch dead code. `v_src` stays as a read-only alias of `v_nom`: the
    console bands and /state already read it that way. `v_in` is the live board voltage."""
    def __init__(self, name, supply=None, v_nom=400.0, r_src=0.35):
        self.name, self.v_nom, self.r_src = name, v_nom, r_src
        self.supply = supply
        self.voltage = v_nom

    @property
    def v_src(self):
        """The fixed rating. Kept under the old name for /state, the console, and the tests."""
        return self.v_nom

    @property
    def v_in(self):
        """The live source voltage from the board. Only step() uses it."""
        return self.v_nom if self.supply is None else self.supply.voltage

    def step(self, i_total):
        # small source noise so baselines learn a live band, not a constant
        self.voltage = self.v_in - i_total * self.r_src + random.gauss(0, 0.15)


class CoolantLoop:
    """A shared loop: pump provides flow; machines take shares and dump heat.
    Degraded pump -> less flow -> everyone's temperature rises (lagged)."""
    def __init__(self, name, flow_nominal=120.0):
        self.name, self.flow_nominal = name, flow_nominal
        self.pump_health = 1.0            # 1.0 healthy .. 0.3 badly degraded
        self.flow = flow_nominal
        self.driver = None                # the chiller that drives the loop (PS2)

    def step(self):
        # A tripped driver leaves only the residual flow. One gauss draw per step either way,
        # so the random stream does not change.
        scale = CHILLER_RESIDUAL_FLOW if self.driver is not None and self.driver.tripped else 1.0
        self.flow = max(self.flow_nominal * self.pump_health * scale + random.gauss(0, 0.8), 5.0)


class Overload:
    """A motor overload relay with thermal memory (SCENARIOS.md 2.2). It uses no random numbers.
    Above pickup x rated current the heat rises at up_per_s. Below it the heat drains at
    down_per_s. At the trip level the relay latches until reset()."""
    def __init__(self, i_rated, pickup=None, up_per_s=None, down_per_s=None, trip=None):
        self.i_rated = i_rated
        self.pickup = CHILLER_OL_PICKUP if pickup is None else pickup
        self.up_per_s = CHILLER_OL_UP_PER_S if up_per_s is None else up_per_s
        self.down_per_s = CHILLER_OL_DOWN_PER_S if down_per_s is None else down_per_s
        self.trip = CHILLER_OL_TRIP if trip is None else trip
        self.heat = 0.0
        self.latched = False

    def step(self, amps, dt):
        """Integrate one tick of motor current. Returns True while the relay is tripped."""
        if amps > self.pickup * self.i_rated:
            self.heat += self.up_per_s * dt
        else:
            self.heat = max(0.0, self.heat - self.down_per_s * dt)
        if self.heat >= self.trip:
            self.latched = True
        return self.latched

    def ratio(self):
        """Heat as a share of the trip level, 0..1. Display only."""
        return min(1.0, self.heat / self.trip) if self.trip > 0 else 1.0

    def reset(self):
        self.heat, self.latched = 0.0, False


class Device:
    """One asset. Draws current from its rail; optionally cooled by the loop.
    duty(t) in [0,1] scales electrical load. Faults multiply friction/heat.
    A device in a cell also follows its PLC: RUN and SPEED_PCT set the drive speed."""
    def __init__(self, name, rail, i_base, loop=None, tau=45.0, heat_k=0.55,
                 duty=None, v_sensitive=False, kind=None):
        # heat_k calibration: steady temps must sit ~50-65C with healthy flow so the PS5
        # story works (pump degrades -> share drops -> temps CROSS TRIP_C=78, not live
        # above it). At heat_k=0.55: press-1 steady = 35 + 0.55*42 = 58C; under PS5
        # (share 0.45) = 35 + 0.55*42/0.45 = 86C -> trips. The old 6.0 put steady at
        # ~287C, permanently past trip - the forecast target was unreachable.
        self.name, self.rail, self.loop = name, rail, loop
        self.kind = kind or name.rsplit("-", 1)[0]
        self.i_base = i_base              # amps at duty=1, healthy
        self.tau = tau                    # thermal time constant (s) -> REAL lags
        self.heat_k = heat_k
        self.duty_fn = duty or (lambda t: 1.0)
        self.v_sensitive = v_sensitive    # degrades visibly under rail sag
        self.friction = 1.0               # PS1 fault raises this
        # A cooled machine starts at its healthy steady temperature (full load, full flow). A
        # plant-sim restart restarts the model, not the plant: the machines were warm before it.
        # A cold start put a 20-minute warm-up ramp on four machines after every restart.
        self.temp = 35.0 + heat_k * i_base if loop is not None else 35.0
        self.current = 0.0
        self.throughput = 100.0           # % of nominal work rate
        self.tripped = False              # PLC trip coil (2F): contactor open
        # 2H cell wiring. The field thread writes cmd_* from the PLC. None means no command.
        self.cell = None                  # the Cell that wires this machine to a PLC
        self.cmd_run = None               # RUN coil (%QX0.k) as last read
        self.cmd_speed_pct = None         # SPEED_PCT word (%QW k) as last read, 0..100
        self.speed_frac = 1.0             # the drive speed after the ramp
        self.overload = None              # a motor overload relay (chiller-1, PS2)
        self.recent = deque(maxlen=REPLAY_N)   # the true current of the last PS4B_REPLAY_S
        self.replay = None                # PS4B: {"buf", "t0"} replayed on the vPLC AMPS word

    def temp_noise(self):
        """Temperature noise per 1 s tick. It scales with sqrt(40 s / tau), so every machine keeps
        the stationary spread of a 40 s machine with 0.05 C per tick (about 0.22 C)."""
        return 0.05 * math.sqrt(40.0 / max(self.tau, 1.0))

    def reported_amps(self, now):
        """The AMPS value that the vPLC field channel receives. PS4B replays a recording of real
        current there, indexed by wall time. Every other reader uses self.current, the truth."""
        if self.replay is None:
            return self.current
        buf = self.replay["buf"]
        return buf[int(max(0.0, now - self.replay["t0"]) / TICK_S) % len(buf)]

    def commanded(self):
        """True while a connected PLC commands this machine."""
        return self.cell is not None and self.cell.connected and self.cmd_run is not None

    def running(self):
        """True when the drive is in RUN: no cell, a RUN command, or a fail-open fallback."""
        if self.cell is None:
            return True
        if self.commanded():
            return bool(self.cmd_run)
        return self.cell.fail_open

    def drive_target(self):
        """The speed the drive moves toward (FLEET.md 7)."""
        if self.cell is None:
            return 1.0
        if self.commanded():
            return self.cmd_speed_pct / 100.0 if self.cmd_run else IDLE_SPEED_FRAC
        return 1.0 if self.cell.fail_open else IDLE_SPEED_FRAC

    def step(self, t, dt):
        # drive ramp: the speed moves toward the target at most RAMP_PER_S per second, then
        # sits exactly on it. A device without a PLC command keeps speed_frac at exactly 1.0.
        target = self.drive_target()
        limit, gap = RAMP_PER_S * dt, target - self.speed_frac
        if abs(gap) <= limit:
            self.speed_frac = target
        else:
            self.speed_frac += limit if gap > 0 else -limit
        if self.tripped:
            # contactor open: no drive current (rail load drops -> voltage RECOVERS, physics),
            # work stops, and the machine cools toward ambient with its own lag
            self.current = max(random.gauss(0.2, 0.02), 0.0)
            self.throughput = max(0.0, self.throughput - 5.0)
            if self.loop is not None:
                self.temp += (35.0 - self.temp) * (dt / self.tau) + random.gauss(0, self.temp_noise())
            return
        duty = self.duty_fn(t)
        running, commanded = self.running(), self.commanded()
        # electrical load: friction directly raises current draw (the PHYSICS
        # of a binding bearing: more torque -> more amps). Never scripted.
        # A current is never below 0 A. The noise alone pushed an idle machine there.
        self.current = max(0.0, self.i_base * duty * self.speed_frac * self.friction
                           + random.gauss(0, 0.05))
        # brownout physics: under-voltage raises current a little (constant
        # power) and cuts throughput for sensitive devices.
        v = self.rail.voltage
        low = v < 0.92 * self.rail.v_src
        if low:
            self.current *= min(1.15, (0.92 * self.rail.v_src) / max(v, 1.0))
        if low and self.v_sensitive and running:
            brownout = max(20.0, 100.0 * v / self.rail.v_src - random.uniform(0, 3))
            self.throughput = brownout * self.speed_frac if commanded else brownout
        elif commanded:
            self.throughput = 100.0 * self.speed_frac
        else:
            # A machine that the voltage does not slow recovers 2 points per tick at ANY rail
            # voltage. Rail A idles under the brownout line, so the old voltage gate kept a press
            # near 0 % after a trip while it drew full current (LOG-075).
            self.throughput = min(100.0, self.throughput + 2.0)
        # the overload relay sees the final motor current. A trip opens the contactor from the
        # next tick on, and the tripped branch above holds the machine off.
        if self.overload is not None and self.overload.step(self.current, dt):
            self.tripped = True
        if not running:
            self.throughput = 0.0         # no RUN command: the machine does no work
        # thermal: first-order response to heat load over the flow share
        if self.loop is not None:
            heat = self.heat_k * self.current
            share = max(self.loop.flow / self.loop.flow_nominal, 0.05)
            t_target = 35.0 + heat / share
            self.temp += (t_target - self.temp) * (dt / self.tau) + random.gauss(0, self.temp_noise())


# ------------------------------------------------------------- build the plant
SUPPLY = Supply()                                # the incoming board above every rail (PS7)
RAIL_A = Rail("psu-a", SUPPLY)
RAIL_B = Rail("psu-b", SUPPLY)
RAIL_C = Rail("psu-c", SUPPLY, v_nom=400.0, r_src=0.5)   # spare feeder for new cells, idle at start
RAILS = [RAIL_A, RAIL_B, RAIL_C]
RAIL_BY_NAME = {r.name: r for r in RAILS}
LOOP = CoolantLoop("cool-1")

def compressor_duty(t):
    # PS2's aggressor personality: OFF most of the time, heavy when the header
    # "calls" - a 300s cycle with a 60s high-draw window (no matured baseline).
    return 1.0 if (t % 300) < 60 else 0.12

# The 8 base devices. The ORDER is a contract: the OpenPLC map (MW8..15, MW24..31) and
# scada/tags.py depend on it. Never append cell machines here.
# Thermal time constants are 90 to 270 s (2026-09-19, LOG-070). At 30 to 90 s, OpenPLC tripped a
# hot machine in 40 to 95 s, before the engine could hold a verdict (about 80 s with GATE_Q=35),
# so PS1, PS2 and PS5 lost the race on the box. Real motors and furnaces are slower still.
BASE_DEVICES = [
    Device("press-1",      RAIL_A, i_base=42.0, loop=LOOP, tau=120.0),
    Device("press-2",      RAIL_A, i_base=38.0, loop=LOOP, tau=165.0),
    Device("cnc-1",        RAIL_A, i_base=25.0, loop=LOOP, tau=90.0, v_sensitive=True),
    Device("qa-scanner-1", RAIL_A, i_base=6.0,  v_sensitive=True),
    Device("conveyor-1",   RAIL_B, i_base=18.0),
    Device("compressor-1", RAIL_B, i_base=55.0, duty=compressor_duty),
    Device("furnace-1",    RAIL_B, i_base=30.0, loop=LOOP, tau=270.0, heat_k=1.0),   # runs hot: steady 65C, PS5 -> ~102C
    Device("chiller-1",    RAIL_B, i_base=22.0),   # drives LOOP: its trip cuts the flow (PS2)
]
DEVICES = BASE_DEVICES          # old name, kept for callers. It holds the base devices only.
CELL_DEVICES = []               # machines that POST /cells adds, in the order they arrive
BASE_BY_NAME = {d.name: d for d in BASE_DEVICES}
BY_NAME = dict(BASE_BY_NAME)    # every device, base and cell
CELLS = {}                      # cell name -> Cell, base cells first
BASE_BY_NAME["chiller-1"].overload = Overload(BASE_BY_NAME["chiller-1"].i_base)
LOOP.driver = BASE_BY_NAME["chiller-1"]


def all_devices():
    """Base devices in their fixed order, then cell machines. Call it under _lock."""
    return BASE_DEVICES + CELL_DEVICES


# ------------------------------------------------------ the field network (PS3) --
def mm1k(rho, k):
    """An M/M/1/K queue at utilization rho with room for k frames. Returns (drop probability,
    mean frames in the system). Above rho 1 it works in 1/rho, so a large rho never overflows."""
    if rho <= 0.0:
        return 0.0, 0.0
    if abs(rho - 1.0) < 1e-6:
        return 1.0 / (k + 1), k / 2.0
    if rho < 1.0:
        rk1 = rho ** (k + 1)
        return (1.0 - rho) * rho ** k / (1.0 - rk1), rho / (1.0 - rho) - (k + 1) * rk1 / (1.0 - rk1)
    r = 1.0 / rho
    rk1 = r ** (k + 1)
    return (1.0 - r) / (1.0 - rk1), (k + 1) / (1.0 - rk1) - 1.0 / (1.0 - r)


def expected_latency(wait, q, rto, timeout):
    """The mean wait of one request: the queue wait, plus a retransmit wait that doubles after
    each loss. q is the chance that the request or its response is lost. A request whose wait
    reaches the timeout counts at the timeout."""
    total, reach, extra, losses = 0.0, 1.0, 0.0, 0
    while reach > 1e-9:
        d = wait + extra
        if d >= timeout:
            return total + reach * timeout
        total += reach * (1.0 - q) * d
        reach *= q
        extra += rto * 2 ** losses
        losses += 1
    return total


class Segment:
    """A shared field network segment (SCENARIOS.md 2.3). The queue is a model: M/M/1/K with
    capacity_fps service and room for `buffer` frames. Its delays and drops act on the real
    Modbus requests of the member cells (SegmentClient). The segment has its own lock and its
    own random generator, so it never touches the physics stream. Take _lock first when you
    need both locks."""
    def __init__(self, name, talkers, capacity_fps, buffer):
        self.name, self.capacity_fps, self.buffer = name, float(capacity_fps), int(buffer)
        self.talkers = {t: {"from": NET_TALKER_FPS, "to": NET_TALKER_FPS, "t0": 0.0, "over": 0.0,
                            "noise": 0.0} for t in talkers}
        self.rng = random.Random(f"segment:{name}")
        self.lock = threading.Lock()
        self._frames = {}         # member -> deque of (wall time, frames) over NET_WINDOW_S
        self._waits = {}          # member -> the waits of its last 4 requests (one sync), s
        self._noise_at = None     # wall time of the last noise draw

    @staticmethod
    def _base(t, now):
        """A talker's load on its ramp, without noise."""
        if t["over"] <= 0.0:
            return t["to"]
        f = min(1.0, max(0.0, (now - t["t0"]) / t["over"]))
        return t["from"] + (t["to"] - t["from"]) * f

    def storm(self, talker, fps, over_s, now=None):
        """Ramp a talker from its present load to fps over over_s seconds (PS3)."""
        now = time.time() if now is None else now
        with self.lock:
            t = self.talkers[talker]
            t.update({"from": self._base(t, now), "to": float(fps), "t0": now, "over": float(over_s)})

    def calm(self):
        """Put every talker back to its load at rest."""
        with self.lock:
            for t in self.talkers.values():
                t.update({"from": NET_TALKER_FPS, "to": NET_TALKER_FPS, "t0": 0.0, "over": 0.0})

    def _model(self, now):
        """(talker loads, member loads, rho, drop probability, queue wait in s). Call it under
        self.lock. The talker noise changes once per second."""
        if self._noise_at is None or not 0.0 <= now - self._noise_at < 1.0:
            self._noise_at = now
            for t in self.talkers.values():
                t["noise"] = self.rng.gauss(0.0, NET_TALKER_NOISE)
        talk = {n: max(0.0, self._base(t, now) * (1.0 + t["noise"])) for n, t in self.talkers.items()}
        mem = {}
        for m, q in self._frames.items():
            while q and now - q[0][0] > NET_WINDOW_S:
                q.popleft()
            mem[m] = sum(n for _, n in q) / NET_WINDOW_S
        lam = sum(talk.values()) + sum(mem.values())
        rho = lam / self.capacity_fps
        p_drop, in_system = mm1k(rho, self.buffer)
        lam_eff = lam * (1.0 - p_drop)
        wait = in_system / lam_eff if lam_eff > 0.0 else 1.0 / self.capacity_fps
        return talk, mem, rho, p_drop, wait

    def request_delay(self, member, timeout, now=None):
        """Sample the wait of one request and its response. Each loss adds a retransmit wait
        that doubles. Returns the wait in s, capped at `timeout`. A wait at the cap is a drop.
        It records the frames that the member offered, retransmits included."""
        now = time.time() if now is None else now
        with self.lock:
            _, _, _, p_drop, wait = self._model(now)
            q = 1.0 - (1.0 - p_drop) ** 2
            delay, losses = wait, 0
            while delay < timeout and losses < 16 and self.rng.random() < q:
                delay += NET_RTO_S * 2 ** losses
                losses += 1
            delay = min(delay, timeout)
            self._frames.setdefault(member, deque()).append((now, 2 * (1 + losses)))
            self._waits.setdefault(member, deque(maxlen=4)).append(delay)
        return delay

    def to_json(self, plcs, timeout, now=None):
        """The /state entry (SCENARIOS.md 3.1). `plcs` maps each member PLC name to its cell.
        A talker's latency is the model mean. A PLC's latency is the mean wait of its last sync."""
        now = time.time() if now is None else now
        with self.lock:
            talk, mem, rho, p_drop, wait = self._model(now)
            expect = expected_latency(wait, 1.0 - (1.0 - p_drop) ** 2, NET_RTO_S, timeout)
            members = {n: {"kind": "talker", "offered_fps": round(fps, 1),
                           "latency_ms": round(expect * 1000.0, 1)} for n, fps in talk.items()}
            for plc, cell in plcs.items():
                waits = self._waits.get(plc)
                lat = sum(waits) / len(waits) if waits else expect
                members[plc] = {"kind": "plc", "cell": cell.name,
                                "offered_fps": round(mem.get(plc, 0.0), 1),
                                "latency_ms": round(lat * 1000.0, 1),
                                "sync_age_s": round(now - cell.last_ok, 1) if cell.last_ok else None,
                                "failures": cell.failures}
        return {"capacity_fps": self.capacity_fps, "utilization": round(rho, 4),
                "latency_ms": round(wait * 1000.0, 2), "drop_ratio": round(p_drop, 4),
                "members": members}


class SegmentClient:
    """A cell's Modbus client behind a segment. Before each real request it waits the queue
    delay plus the retransmit delay, with cell.stop.wait(). A delay that reaches CELL_TIMEOUT_S
    raises ConnectionError, and the cell disconnects as it does for a real timeout.
    Every other attribute (connect, close, connected) passes through."""
    _REQUESTS = frozenset({"write_registers", "write_coils", "read_holding_registers", "read_coils"})

    def __init__(self, segment, cell, client):
        self.segment, self.cell, self.client = segment, cell, client
        self.member = cell.controller

    def __getattr__(self, name):
        attr = getattr(self.client, name)
        if name in self._REQUESTS:
            return lambda *a, **kw: self._request(attr, a, kw)
        return attr

    def _request(self, call, args, kwargs):
        timeout = CELL_TIMEOUT_S
        delay = self.segment.request_delay(self.member, timeout)
        if delay >= timeout:
            self.cell.stop.wait(timeout)
            raise ConnectionError("simulated segment drop")
        if self.cell.stop.wait(delay):
            raise ConnectionError("cell removed")
        return call(*args, **kwargs)


def parse_segments(spec):
    """Parse a FIELD_SEGMENTS spec: name|talker,talker|capacity_fps|buffer;...
    A bad entry is skipped with a log line."""
    out = []
    taken = set(BY_NAME) | set(RAIL_BY_NAME) | {LOOP.name, SUPPLY.name, "plant-sim"}
    for raw in (spec or "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            print(f"plant-sim: FIELD_SEGMENTS entry skipped, need 4 fields: {raw!r}", flush=True)
            continue
        name, talkers, cap, buf = parts
        talkers = [t.strip() for t in talkers.split(",") if t.strip()]
        names = [name] + talkers
        try:
            cap, buf = float(cap), int(buf)
        except ValueError:
            cap, buf = 0.0, 0
        if (not all(_NAME_RE.match(n) for n in names) or len(set(names)) != len(names)
                or taken & set(names) or not math.isfinite(cap) or cap <= 0.0 or buf < 1):
            print(f"plant-sim: FIELD_SEGMENTS entry skipped, bad or used name or number: {raw!r}",
                  flush=True)
            continue
        taken |= set(names)
        out.append({"name": name, "talkers": talkers, "capacity_fps": cap, "buffer": buf})
    return out


SEGMENTS = {s["name"]: Segment(s["name"], s["talkers"], s["capacity_fps"], s["buffer"])
            for s in parse_segments(FIELD_SEGMENTS)}
FIELD_NET = next(iter(SEGMENTS.values()), None)   # the segment that carries every cell PLC link
NET_NAMES = set(SEGMENTS) | {t for s in SEGMENTS.values() for t in s.talkers}   # reserved names
# PS3 floods the first talker of the first segment that has one (hmi-gw by default)
STORM = next(((s, next(iter(s.talkers))) for s in SEGMENTS.values() if s.talkers), None)


def segment_plcs(seg):
    """The member PLCs of a segment, each with its first cell. Every cell's PLC is on the
    first segment, the field network. Call it under _lock."""
    plcs = {}
    if seg is FIELD_NET:
        for c in CELLS.values():
            plcs.setdefault(c.controller, c)
    return plcs


# ------------------------------------------------------------------ PS faults --
def _ps4b_apply():
    """press-1 friction rises, and its vPLC AMPS word replays the last PS4B_REPLAY_S of real
    current. The recording is the true current up to the moment of the fault."""
    d = BY_NAME["press-1"]
    d.friction = PS4B_FRICTION
    d.replay = {"buf": list(d.recent) or [d.current], "t0": time.time()}


def _ps4b_clear():
    d = BY_NAME["press-1"]
    d.friction, d.replay = 1.0, None


# PS-series faults: each perturbs the MODEL; the chain emerges. (LOG-028 map, SCENARIOS.md 2.)
FAULTS = {
    "PS1": {"desc": "press-1 bearing friction rises -> rail-A sag cascade",
            "apply": lambda: setattr(BY_NAME["press-1"], "friction", 1.9),
            "clear": lambda: setattr(BY_NAME["press-1"], "friction", 1.0)},
    "PS2": {"desc": "compressor stuck-on: rail B stays low -> chiller-1 overload trips -> "
                    "coolant flow drops -> cooled machines heat",
            "apply": lambda: setattr(BY_NAME["compressor-1"], "duty_fn", lambda t: 1.0),
            "clear": lambda: setattr(BY_NAME["compressor-1"], "duty_fn", compressor_duty)},
    "PS3": {"desc": f"{STORM[1] if STORM else 'talker'} floods segment "
                    f"{STORM[0].name if STORM else 'field'}: {NET_TALKER_FPS:.0f} -> "
                    f"{NET_STORM_FPS:.0f} frames/s over {NET_RAMP_S:.0f} s -> cell links lag and drop",
            "apply": lambda: STORM[0].storm(STORM[1], NET_STORM_FPS, NET_RAMP_S),
            "clear": lambda: STORM[0].calm()},
    "PS4B": {"desc": f"press-1 friction {PS4B_FRICTION} while its vPLC AMPS word replays the "
                     f"last {PS4B_REPLAY_S:.0f} s -> the feeder does not balance",
             "apply": _ps4b_apply,
             "clear": _ps4b_clear},
    "PS5": {"desc": "chiller pump degrades -> coolant flow drops -> temps ramp to trip",
            "apply": lambda: setattr(LOOP, "pump_health", 0.45),
            "clear": lambda: setattr(LOOP, "pump_health", 1.0)},
    "PS7": {"desc": f"the supply dips to {SUPPLY_DIP_PCT * 100:.0f} % of nominal -> every rail sags "
                    f"together, with no machine leading -> chiller-1 overload trips -> coolant flow "
                    f"drops -> cooled machines heat",
            "apply": lambda: setattr(SUPPLY, "target", SUPPLY_DIP_PCT),
            "clear": lambda: setattr(SUPPLY, "target", 1.0)},
}
if STORM is None:
    del FAULTS["PS3"]             # FIELD_SEGMENTS is empty: there is no segment to flood
ACTIVE = set()
TRIP_C = 78.0     # the PS5 forecast target: coolant-side trip threshold


def inject_fault(fid):
    """Apply one PS fault (POST /fault/<id>). Call it under _lock. Returns False and changes
    nothing when the fault is already active. The console can send a fault twice (a double
    click). A second PS4B apply recorded the replay from the faulted current, so the vPLC word
    matched the truth and the PS4B evidence was gone (LOG-075)."""
    if fid in ACTIVE:
        return False
    FAULTS[fid]["apply"]()
    ACTIVE.add(fid)
    return True

# ------------------------------------------------------------------ sim loop --
_lock = threading.Lock()
T0 = time.time()

# Register/coil order is the contract with plc/program.st — see plc/REGISTER_MAP.md.
COOLED = ["press-1", "press-2", "cnc-1", "furnace-1"]


def plc_frames():
    """The OpenPLC %MW writes for one tick: (MW0..15, MW24..31). Call it under _lock.
    Only BASE_DEVICES feed these blocks, so their length and order never depend on cells."""
    regs = [max(0, int(BASE_BY_NAME[n].temp * 10)) for n in COOLED]          # MW0..3 temp x10
    regs += [max(0, int(LOOP.flow * 10)), max(0, int(LOOP.pump_health * 100)),
             max(0, int(RAIL_A.voltage * 10)), max(0, int(RAIL_B.voltage * 10))]  # MW4..7
    regs += [max(0, int(d.current * 10)) for d in BASE_DEVICES]               # MW8..15 amps x10
    # MW24..31 throughput x10 (2F.2 tag server). A SEPARATE write: one 32-word
    # block would sweep 0 over MW20 (reset_cmd) every tick and race the pulse.
    thru = [max(0, int(d.throughput * 10)) for d in BASE_DEVICES]
    return regs, thru


def plc_sync_once(client):
    """One OpenPLC field-wiring tick over an open client: sensor words out, trip coils in."""
    with _lock:
        regs, thru = plc_frames()
    client.write_registers(PLC_MW_BASE, regs, slave=1)
    client.write_registers(PLC_MW_BASE + 24, thru, slave=1)
    if _PLC_RESET.is_set():
        client.write_registers(PLC_MW_BASE + 20, [1], slave=1)            # MW20: reset cmd
        _PLC_RESET.clear()
    rr = client.read_coils(0, count=len(COOLED), slave=1)                 # QX0.0..0.3 trips
    if not rr.isError():
        bits = list(rr.bits)[:len(COOLED)]
        with _lock:
            for name, b in zip(COOLED, bits):
                BASE_BY_NAME[name].tripped = bool(b)
            PLC_STATE.update(connected=True, mode="closed-loop")


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
            plc_sync_once(client)
        except Exception:
            with _lock:
                PLC_STATE.update(connected=False, mode="reconnecting")
                for name in COOLED:
                    BASE_BY_NAME[name].tripped = False
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            client = None
            time.sleep(5.0)
        time.sleep(TICK_S)


def step_world(t, dt):
    """One physics tick over the supply, every rail, the loop, and every device.
    Call it under _lock."""
    devices = all_devices()
    # The board first: every rail reads its voltage this tick. Supply.step draws no random
    # numbers, so at target 1.0 the rail noise stream is unchanged (SCENARIOS.md 2.8).
    SUPPLY.step(sum(d.current for d in devices), dt)
    for rail in RAILS:
        rail.step(sum(d.current for d in devices if d.rail is rail))
    LOOP.step()
    for d in devices:
        d.step(t, dt)
        d.recent.append(d.current)        # the PS4B recording. It draws no random numbers.


def tick_dt(now, last):
    """The physics step of one loop() pass: the wall time since the last pass, at least 1 ms and
    at most MAX_DT_S. A stalled process (CPU starvation, a paused container) must not integrate
    its stall as one step. Forward Euler overshoots once dt passes tau, and one 120 s step in a
    compressor window would give the chiller relay more than a trip's worth of heat at once
    (LOG-075)."""
    return min(max(now - last, 1e-3), MAX_DT_S)


def loop():
    last = time.time()
    while True:
        now = time.time()
        dt, last = tick_dt(now, last), now
        t = now - T0
        with _lock:
            step_world(t, dt)
        time.sleep(max(0.0, TICK_S - (time.time() - now)))

# ----------------------------------------------------------------------- cells --
class CellError(Exception):
    """A refused cell request. `code` is the HTTP status for the answer."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def controller_name(host):
    """The PLC name from its host: plc-stamping.fleet.svc.cluster.local -> plc-stamping.
    An IP address has no name, so it stays whole."""
    return host if _IPV4_RE.match(host) else host.split(".", 1)[0]


class Cell:
    """A group of machines that one PLC commands through its field port (FLEET.md 3.1, 4.1).
    Slot k of `devices` is machine index k in the wiring convention."""
    def __init__(self, name, plc_host, field_port, rail, fail_open, devices, base):
        self.name, self.plc_host, self.field_port = name, plc_host, field_port
        self.rail, self.fail_open, self.base = rail, fail_open, base
        self.devices = list(devices)
        self.connected = False
        self.last_ok = None               # wall time of the last good sync
        self.last_error = None            # the last sync failure, as text
        self.failures = 0                 # failed syncs since the cell started
        self.stop = threading.Event()     # set when the cell is removed
        self.thread = None

    @property
    def controller(self):
        return controller_name(self.plc_host)

    @property
    def mode(self):
        """closed-loop: the PLC commands the machines. fail-open: no PLC, machines run at
        full speed. idle: no PLC, machines stay idle."""
        if self.connected:
            return "closed-loop"
        return "fail-open" if self.fail_open else "idle"

    def to_json(self):
        return {"plc": self.controller, "plc_host": self.plc_host, "field_port": self.field_port,
                "rail": self.rail.name, "connected": self.connected, "mode": self.mode,
                "fail_open": self.fail_open, "base": self.base,
                "machines": [d.name for d in self.devices],
                "last_ok": self.last_ok, "last_error": self.last_error}


def parse_base_cells(spec):
    """Parse a BASE_CELLS spec into cell dicts. A bad entry is skipped with a log line."""
    out = []
    for raw in (spec or "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 4:
            print(f"plant-sim: BASE_CELLS entry skipped, need 4 fields: {raw!r}", flush=True)
            continue
        name, hostport, policy, machines = parts
        host, sep, port = hostport.rpartition(":")
        if not sep:
            host, port = hostport, str(DEFAULT_FIELD_PORT)
        if (not _NAME_RE.match(name) or not _HOST_RE.match(host) or not port.isdigit()
                or not 0 < int(port) < 65536):
            print(f"plant-sim: BASE_CELLS entry skipped, bad name or host:port: {raw!r}", flush=True)
            continue
        out.append({"cell": name, "plc_host": host, "field_port": int(port),
                    "fail_open": policy.lower().replace("-", "").replace("_", "") == "failopen",
                    "machines": [m.strip() for m in machines.split(",") if m.strip()]})
    return out


def init_cells(spec, join_s=0.0):
    """Remove every cell, then build the base cells from a BASE_CELLS spec. It starts no
    threads (main() does). Import calls it once. Tests call it to get a clean world."""
    with _lock:
        old = list(CELLS.values())
        for c in old:
            c.stop.set()
        CELLS.clear()
        for d in CELL_DEVICES:
            BY_NAME.pop(d.name, None)
        CELL_DEVICES.clear()
        for d in BASE_DEVICES:
            d.cell, d.cmd_run, d.cmd_speed_pct, d.speed_frac = None, None, None, 1.0
        for s in parse_base_cells(spec):
            if (s["cell"] in CELLS or s["cell"] in BY_NAME or s["cell"] in RAIL_BY_NAME
                    or s["cell"] in NET_NAMES or s["cell"] in (LOOP.name, "plant-sim")):
                print(f"plant-sim: BASE_CELLS cell {s['cell']} skipped, name in use", flush=True)
                continue
            devs = []
            for m in s["machines"]:
                d = BASE_BY_NAME.get(m)
                if d is None or d.cell is not None or d in devs or len(devs) >= CELL_SLOTS:
                    print(f"plant-sim: BASE_CELLS machine {m} skipped for cell {s['cell']}", flush=True)
                    continue
                devs.append(d)
            if not devs:
                continue
            cell = Cell(s["cell"], s["plc_host"], s["field_port"], devs[0].rail,
                        s["fail_open"], devs, base=True)
            for d in devs:
                d.cell = cell
            CELLS[cell.name] = cell
    for c in old:
        if join_s and c.thread is not None:
            c.thread.join(join_s)


def _num(body, key, default, lo, hi, where, lo_ok=False):
    """A finite number in (lo, hi], or [lo, hi] when lo_ok. A missing or null key uses default."""
    v = body.get(key)
    v = default if v is None else v
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise CellError(400, f"{where}{key} must be a number")
    if not ((lo <= v) if lo_ok else (lo < v)) or v > hi:
        raise CellError(400, f"{where}{key} must be in {'[' if lo_ok else '('}{lo}, {hi}]")
    return float(v)


def _flag(body, key, where):
    """A boolean. A missing or null key is false."""
    v = body.get(key)
    if v is None:
        return False
    if not isinstance(v, bool):
        raise CellError(400, f"{where}{key} must be true or false")
    return v


def validate_cell_body(body):
    """Check a POST /cells body (FLEET.md 7). Returns a clean spec or raises CellError(400)."""
    if not isinstance(body, dict):
        raise CellError(400, "body must be a JSON object")
    name = body.get("cell")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise CellError(400, "cell must match ^[a-z0-9]([-a-z0-9]{0,30}[a-z0-9])?$")
    host = body.get("plc_host")
    if not isinstance(host, str) or not _HOST_RE.match(host):
        raise CellError(400, "plc_host must be a host name or an IPv4 address")
    port = body.get("field_port")
    port = DEFAULT_FIELD_PORT if port is None else port
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise CellError(400, "field_port must be an integer 1..65535")
    rail = body.get("rail") or RAIL_C.name
    if not isinstance(rail, str) or rail not in RAIL_BY_NAME:
        raise CellError(400, f"rail must be one of {sorted(RAIL_BY_NAME)}")
    machines = body.get("machines")
    if not isinstance(machines, list) or not 1 <= len(machines) <= CELL_SLOTS:
        raise CellError(400, f"machines must be a list of 1..{CELL_SLOTS} machines")
    clean, seen = [], set()
    for k, m in enumerate(machines):
        where = f"machines[{k}]."
        if not isinstance(m, dict):
            raise CellError(400, f"machines[{k}] must be an object")
        mname = m.get("name")
        if not isinstance(mname, str) or not _NAME_RE.match(mname):
            raise CellError(400, f"{where}name must match ^[a-z0-9]([-a-z0-9]{{0,30}}[a-z0-9])?$")
        if mname in seen or mname == name:
            raise CellError(400, f"{where}name {mname} is used twice in this body")
        seen.add(mname)
        kind = m.get("kind") or mname.rsplit("-", 1)[0]
        if not isinstance(kind, str) or not 0 < len(kind) <= 32:
            raise CellError(400, f"{where}kind must be a string of 1..32 characters")
        clean.append({"name": mname, "kind": kind,
                      "i_base": _num(m, "i_base", None, 0.0, 200.0, where),
                      "cooled": _flag(m, "cooled", where),
                      # tau >= MIN_TAU_S keeps the explicit thermal step stable (dt/tau <= 0.5)
                      "tau": _num(m, "tau", 45.0, MIN_TAU_S, 3600.0, where, lo_ok=True),
                      "heat_k": _num(m, "heat_k", 0.55, 0.0, 10.0, where, lo_ok=True),
                      "v_sensitive": _flag(m, "v_sensitive", where)})
    return {"cell": name, "plc_host": host, "field_port": port, "rail": rail,
            "fail_open": _flag(body, "fail_open", ""), "machines": clean}


def add_cell(body, start=True):
    """Add a cell of new machines (POST /cells). The machines start idle. When `start` is
    true, a field-wiring thread connects to the PLC. Raises CellError on a refusal."""
    s = validate_cell_body(body)
    with _lock:
        taken = set(BY_NAME) | set(CELLS) | set(RAIL_BY_NAME) | NET_NAMES | {LOOP.name, SUPPLY.name, "plant-sim"}
        if s["cell"] in CELLS:
            raise CellError(409, f"cell {s['cell']} exists")
        if s["cell"] in taken:
            raise CellError(409, f"cell name {s['cell']} is in use by another asset")
        for m in s["machines"]:
            if m["name"] in taken:
                raise CellError(409, f"machine name {m['name']} is in use")
        rail = RAIL_BY_NAME[s["rail"]]
        devs = []
        for m in s["machines"]:
            d = Device(m["name"], rail, i_base=m["i_base"], loop=LOOP if m["cooled"] else None,
                       tau=m["tau"], heat_k=m["heat_k"], v_sensitive=m["v_sensitive"],
                       kind=m["kind"])
            # idle and at ambient until the PLC runs it
            d.speed_frac, d.throughput, d.temp = IDLE_SPEED_FRAC, 0.0, 35.0
            devs.append(d)
        cell = Cell(s["cell"], s["plc_host"], s["field_port"], rail, s["fail_open"], devs,
                    base=False)
        for d in devs:
            d.cell = cell
            BY_NAME[d.name] = d
        CELL_DEVICES.extend(devs)
        CELLS[cell.name] = cell
    if start:
        start_cell(cell)
    return cell


def remove_cell(name):
    """Remove a cell and its machines (DELETE /cells/<cell>). Base cells are refused."""
    with _lock:
        cell = CELLS.get(name)
        if cell is None:
            raise CellError(404, f"no cell {name}")
        if cell.base:
            raise CellError(403, f"cell {name} is a base cell and cannot be removed")
        cell.stop.set()
        del CELLS[name]
        for d in cell.devices:
            if d in CELL_DEVICES:
                CELL_DEVICES.remove(d)
            if BY_NAME.get(d.name) is d:
                del BY_NAME[d.name]
    return cell


def start_cell(cell):
    """Start the field-wiring thread of one cell."""
    cell.thread = threading.Thread(target=cell_loop, args=(cell,), name=f"cell-{cell.name}",
                                   daemon=True)
    cell.thread.start()


def _word10(x):
    """Scale a value x10 into an INT register, clamped to 0..32767."""
    return max(0, min(32767, int(x * 10)))


def cell_frames(cell, now=None):
    """The field-port writes for one tick: (holding 0..31, coils 0..7). Call it under _lock.
    Machine k fills AMPS, TEMP, VOLTS, THROUGHPUT at 4k..4k+3 and READY at coil k.
    An empty slot stays 0. AMPS is the reported value, which PS4B replays (wall time `now`)."""
    now = time.time() if now is None else now
    hr, coils = [0] * (4 * CELL_SLOTS), [False] * CELL_SLOTS
    for k, d in enumerate(cell.devices[:CELL_SLOTS]):
        hr[4 * k] = _word10(d.reported_amps(now))
        hr[4 * k + 1] = _word10(d.temp) if d.loop is not None else 0
        hr[4 * k + 2] = _word10(d.rail.voltage)
        hr[4 * k + 3] = _word10(d.throughput)
        coils[k] = not d.tripped
    return hr, coils


def _ok(resp, what):
    """Return a good Modbus response. Raise ConnectionError for an error response."""
    if resp is None or resp.isError():
        raise ConnectionError(f"{what}: {resp}")
    return resp


def cell_sync_once(cell, client):
    """One field-wiring tick for a cell over an open client (FLEET.md 4.1).
    Writes the sensor words and READY coils, then reads SPEED_PCT and RUN back."""
    with _lock:
        if cell.stop.is_set():
            return False
        hr, coils = cell_frames(cell)
    _ok(client.write_registers(0, hr, slave=1), "write holding 0..31")
    _ok(client.write_coils(0, coils, slave=1), "write coils 0..7")
    words = list(_ok(client.read_holding_registers(100, count=CELL_SLOTS, slave=1),
                     "read holding 100..107").registers)
    bits = list(_ok(client.read_coils(100, count=CELL_SLOTS, slave=1), "read coils 100..107").bits)
    if len(words) < CELL_SLOTS or len(bits) < CELL_SLOTS:
        raise ConnectionError("short read from the field port")
    speeds = [max(0, min(100, w - 65536 if w >= 32768 else w)) for w in words[:CELL_SLOTS]]
    with _lock:
        if cell.stop.is_set():
            return False
        for k, d in enumerate(cell.devices[:CELL_SLOTS]):
            if d.cell is cell:
                d.cmd_run, d.cmd_speed_pct = bool(bits[k]), speeds[k]
        cell.connected, cell.last_ok, cell.last_error = True, time.time(), None
    return True


def cell_disconnected(cell, error):
    """Mark a cell offline. Its machines fall back to fail-open or idle."""
    with _lock:
        if cell.stop.is_set():
            return
        cell.connected, cell.last_error = False, error
        cell.failures += 1
        for d in cell.devices:
            if d.cell is cell:
                d.cmd_run, d.cmd_speed_pct = None, None


def cell_loop(cell):
    """The field wiring of one cell: sync every TICK_S. On a failure, mark the cell
    disconnected and retry after CELL_RETRY_S. The thread ends when the cell is removed.
    With a field segment, each request goes through it (SegmentClient, PS3)."""
    if ModbusTcpClient is None:
        cell_disconnected(cell, "pymodbus is not installed")
        return
    client = None
    while not cell.stop.is_set():
        try:
            if client is None:
                client = ModbusTcpClient(cell.plc_host, port=cell.field_port,
                                         timeout=CELL_TIMEOUT_S, retries=1)
                if FIELD_NET is not None:
                    client = SegmentClient(FIELD_NET, cell, client)
            if not client.connected and not client.connect():
                raise ConnectionError(f"no route to PLC field port {cell.plc_host}:{cell.field_port}")
            cell_sync_once(cell, client)
        except Exception as e:
            cell_disconnected(cell, f"{type(e).__name__}: {e}"[:300])
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass
            client = None
            cell.stop.wait(CELL_RETRY_S)
            continue
        cell.stop.wait(TICK_S)
    try:
        if client is not None:
            client.close()
    except Exception:
        pass


def domains():
    """Live shared-medium memberships (FLEET.md 7). Each member list ends with the medium.
    rail:<supply> lists every rail, so a cause can sit above the plant.
    The loop lists its driver (chiller-1) before the medium. A segment lists its talkers,
    then its member PLCs (SCENARIOS.md 3.2)."""
    with _lock:
        devices = all_devices()
        out = {f"rail:{r.name}": [d.name for d in devices if d.rail is r] + [r.name] for r in RAILS}
        # PS7: the board and its rails share one electrical medium, so the prefix stays "rail".
        # The suffix names the medium, the same convention as rail:psu-a (SCENARIOS.md 3.2).
        out[f"rail:{SUPPLY.name}"] = [r.name for r in RAILS] + [SUPPLY.name]
        driver = [LOOP.driver.name] if LOOP.driver is not None else []
        out[f"loop:{LOOP.name}"] = [d.name for d in devices if d.loop is LOOP] + driver + [LOOP.name]
        for seg in SEGMENTS.values():
            out[f"net:{seg.name}"] = list(seg.talkers) + list(segment_plcs(seg)) + [seg.name]
    return {"domains": out}


def feeder_amps(rail):
    """The feeder meter of one rail: the sum of the device currents on it now. It is not the
    value that the last rail.step used, which is one tick old. Call it under _lock."""
    return sum(d.current for d in all_devices() if d.rail is rail)


def supply_amps():
    """The board meter: the plant total current now. Same freshness rule as feeder_amps, so the
    board and the rail meters always add up. Call it under _lock."""
    return sum(d.current for d in all_devices())


def cooling_shortfall():
    """Heat that the loop fails to remove, in W (SCENARIOS.md 3.3). Call it under _lock.
    A cooled machine makes heat_k * I. At flow share s it heats as if (1/s) times that heat
    reached it, so the shortfall is heat * (1/s - 1). The sum is never below 0."""
    share = max(LOOP.flow / LOOP.flow_nominal, 0.05)
    heat = sum(d.heat_k * d.current for d in all_devices() if d.loop is LOOP)
    return max(0.0, heat * (1.0 / share - 1.0))


def trip_reason(d):
    """Why a machine is off: "overload" while its own relay holds it, else None."""
    return "overload" if d.tripped and d.overload is not None and d.overload.latched else None


def segments_json(now=None):
    """Every segment as /state JSON. Call it under _lock."""
    return {s.name: s.to_json(segment_plcs(s), CELL_TIMEOUT_S, now) for s in SEGMENTS.values()}


def cells_json():
    """Every cell as JSON. Call it under _lock."""
    return {c.name: c.to_json() for c in CELLS.values()}


init_cells(BASE_CELLS)

# ----------------------------------------------------------------- exposition --
def metrics_text():
    """Prometheus exposition. Labels namespace/pod match what the L2 aggregator
    already extracts from every query result - zero aggregator changes."""
    L = []
    def g(metric, pod, val, extra=""):
        L.append(f'{metric}{{namespace="{NS}",pod="{pod}"{extra}}} {val:.4f}')
    with _lock:
        # PS7: the board above the rails. It rides the same bus_voltage family as the rails, and
        # it carries the only current_draw inside rail:<supply>, so an external cause has an
        # entity to land on (SCENARIOS.md 3.3 and 4.5).
        g("plant_bus_voltage_volts", SUPPLY.name, SUPPLY.voltage)
        g("plant_current_draw_amps", SUPPLY.name, supply_amps())
        g("plant_supply_nominal_volts", SUPPLY.name, SUPPLY.v_nom)
        for r in RAILS:
            g("plant_bus_voltage_volts", r.name, r.voltage)
            g("plant_feeder_current_amps", r.name, feeder_amps(r))   # the independent meter
        g("plant_coolant_flow_lpm", LOOP.name, LOOP.flow)
        g("plant_pump_health_ratio", LOOP.name, LOOP.pump_health)
        for d in all_devices():
            g("plant_current_draw_amps", d.name, d.current)
            g("plant_throughput_pct", d.name, d.throughput)
            # every device also reports the voltage IT sees (its rail's) - the
            # per-victim sag signal the engine correlates against the source's amps
            g("plant_bus_voltage_volts", d.name, d.rail.voltage)
            if d.loop is not None:
                g("plant_temp_celsius", d.name, d.temp)
                g("plant_heat_load_watts", d.name, d.heat_k * d.current)
            if d.commanded():             # only a real PLC command, never a fallback
                g("plant_commanded_speed_pct", d.name, float(d.cmd_speed_pct) if d.cmd_run else 0.0)
        g("plant_trip_threshold_celsius", "cool-1", TRIP_C)
        for n in COOLED:                                  # PLC trip coils, mirrored per machine
            g("plant_trip_active", n, 1.0 if BASE_BY_NAME[n].tripped else 0.0)
        # PS2: the chiller's own overload relay. It is not an OpenPLC trip. The ratio is for
        # display only: it ramps in every normal compressor window.
        for d in all_devices():
            if d.overload is not None:
                g("plant_motor_tripped", d.name, 1.0 if trip_reason(d) else 0.0)
                g("plant_overload_ratio", d.name, d.overload.ratio())
        if LOOP.driver is not None:
            g("plant_cooling_shortfall_watts", LOOP.driver.name, cooling_shortfall())
        for name, seg in segments_json().items():        # PS3: the field segment
            g("plant_net_utilization_ratio", name, seg["utilization"])
            g("plant_net_drop_ratio", name, seg["drop_ratio"])
            for member, m in seg["members"].items():
                g("plant_net_offered_fps", member, m["offered_fps"])
                g("plant_net_latency_ms", member, m["latency_ms"])
        L.append(f'plant_plc_connected{{namespace="{NS}",pod="plant-sim"}} '
                 f'{1 if PLC_STATE["connected"] else 0}')
        for c in CELLS.values():
            L.append(f'plant_cell_connected{{namespace="{NS}",pod="{c.name}"}} '
                     f'{1 if c.connected else 0}')
        for fid in FAULTS:
            L.append(f'plant_fault_active{{namespace="{NS}",pod="plant-sim",fault="{fid}"}} '
                     f'{1 if fid in ACTIVE else 0}')
    return "\n".join(L) + "\n"


def state_json():
    """The /state document. It carries the static topology too (rail/cooled/nominals) so the
    dashboard's Machines section can group by medium without a second source of truth."""
    with _lock:
        return {
            "supply": {"name": SUPPLY.name, "volts": round(SUPPLY.voltage, 2),
                       "nominal_volts": SUPPLY.v_nom, "amps": round(supply_amps(), 2),
                       "target_pct": round(SUPPLY.target * 100.0, 1), "dipped": SUPPLY.dipped()},
            "rails": {r.name: {"volts": round(r.voltage, 2), "v_src": r.v_src,
                               "amps": round(feeder_amps(r), 2)} for r in RAILS},
            "loop": {"name": LOOP.name, "flow": round(LOOP.flow, 1),
                     "flow_nominal": LOOP.flow_nominal,
                     "pump_health": round(LOOP.pump_health, 2)},
            "trip_c": TRIP_C,
            "devices": {d.name: {"amps": round(d.current, 2),
                                 "temp": round(d.temp, 1) if d.loop else None,
                                 "throughput": round(d.throughput, 1),
                                 "rail": d.rail.name,
                                 "cooled": d.loop is not None,
                                 "tripped": d.tripped,
                                 "trip_reason": trip_reason(d),
                                 "kind": d.kind,
                                 "cell": d.cell.name if d.cell else None,
                                 "controller": d.cell.controller if d.cell else None,
                                 "commanded": {"run": d.cmd_run if d.commanded() else None,
                                               "speed_pct": d.cmd_speed_pct if d.commanded() else None},
                                 "speed_pct": round(d.speed_frac * 100.0, 1)}
                        for d in all_devices()},
            "cells": cells_json(),
            "segments": segments_json(),
            "plc": dict(PLC_STATE),
            "active_faults": sorted(ACTIVE)}


def reset_plant():
    """What POST /reset does, except the PLC reset pulse. Call it under _lock.
    It clears every fault, relay trip, and replay, and puts every talker back to rest."""
    for fid in list(ACTIVE):
        FAULTS[fid]["clear"]()
    ACTIVE.clear()
    # Return every asset to a clean, record-ready baseline at once. A machine that a trip
    # knocked down also climbs back by itself at 2 points per tick (Device.step), but a reset
    # must clear the floor now, not after 50 s. A machine that its PLC holds out of RUN keeps 0:
    # it does no work.
    for d in all_devices():
        d.tripped = False
        d.replay = None
        if d.overload is not None:
            d.overload.reset()            # PS2 is clear first, so the chiller does not re-trip
        if not d.running():
            d.throughput = 0.0
        elif d.commanded():
            d.throughput = 100.0 * d.speed_frac
        else:
            d.throughput = 100.0
    for seg in SEGMENTS.values():
        seg.calm()


class H(BaseHTTPRequestHandler):
    timeout = 10          # s. A client that connects and sends nothing frees its thread after this.

    def _send(self, code, body, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def _json(self, code, obj):
        return self._send(code, json.dumps(obj, indent=2), "application/json")

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise CellError(400, "bad Content-Length")
        if not 0 < n <= MAX_BODY:
            raise CellError(400, f"body must be JSON of 1..{MAX_BODY} bytes")
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            raise CellError(400, "body is not valid JSON")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if self.path == "/metrics":
            return self._send(200, metrics_text())
        if self.path == "/healthz":
            return self._send(200, "ok\n")
        if self.path.startswith("/fault"):
            return self._send(200, json.dumps(
                {fid: {"desc": f["desc"], "active": fid in ACTIVE} for fid, f in FAULTS.items()},
                indent=2), "application/json")
        if self.path == "/state":
            return self._json(200, state_json())
        if path == "/cells":
            with _lock:
                body = {"cells": cells_json()}
            return self._json(200, body)
        if path == "/domains":
            return self._json(200, domains())
        self._send(404, "not found\n")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if self.path == "/reset":
            with _lock:
                reset_plant()
            _PLC_RESET.set()     # pulse the PLC's reset word; latched trips clear only if the
            return self._send(200, "all faults cleared; plant returned to baseline; PLC trip reset requested\n")  # condition is gone
        if self.path.startswith("/fault/"):
            fid = self.path.rsplit("/", 1)[-1].upper()
            if fid not in FAULTS:
                return self._send(404, f"unknown fault {fid}; have {sorted(FAULTS)}\n")
            with _lock:
                applied = inject_fault(fid)
            if not applied:
                return self._send(200, f"{fid} is already active. Nothing changed. POST /reset clears it.\n")
            return self._send(200, f"{fid} injected: {FAULTS[fid]['desc']}\n")
        if path == "/cells":
            try:
                cell = add_cell(self._body())
            except CellError as e:
                return self._json(e.code, {"error": str(e)})
            with _lock:
                body = {"cell": cell.name, **cell.to_json()}
            return self._json(201, body)
        self._send(404, "not found\n")

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/cells/"):
            name = path[len("/cells/"):]
            try:
                cell = remove_cell(name)
            except CellError as e:
                return self._json(e.code, {"error": str(e)})
            return self._json(200, {"removed": cell.name,
                                    "machines": [d.name for d in cell.devices]})
        self._send(404, "not found\n")

    def log_message(self, *_):
        pass

def make_server(port=PORT, host="0.0.0.0"):
    """The HTTP server: one thread per request, like the tag server and the vPLC. The old
    single-threaded server let one silent client block /metrics and the /healthz probe."""
    return ThreadingHTTPServer((host, port), H)


def main():
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=plc_loop, daemon=True).start()
    with _lock:
        base_cells = list(CELLS.values())
    for cell in base_cells:
        start_cell(cell)
    cells = ", ".join(f"{c.name}->{c.plc_host}:{c.field_port}"
                      f"{' (fail-open)' if c.fail_open else ''}" for c in base_cells) or "none"
    segs = ", ".join(f"{s.name} ({','.join(s.talkers) or 'no talker'}, {s.capacity_fps:.0f} fps, "
                     f"K={s.buffer})" for s in SEGMENTS.values()) or "none"
    print(f"plant-sim up on :{PORT} | {len(BASE_DEVICES)} base devices, rails "
          f"{'/'.join(r.name for r in RAILS)}, loop cool-1 | cells: {cells} | segments: {segs} | "
          f"faults: {', '.join(sorted(FAULTS))} | plc: {PLC_STATE['mode']}"
          f"{' @ ' + PLC_HOST + ':' + str(PLC_PORT) if PLC_HOST else ''}", flush=True)
    make_server().serve_forever()

if __name__ == "__main__":
    main()
