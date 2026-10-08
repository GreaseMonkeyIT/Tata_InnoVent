"""The utilities plant (ideas.md 13.1 phase 1, item 6): the supply, the shared utilities and the
links between them, before the line machines exist.

Wiring (ideas.md 11.4 and 11.7):
  incomer-1 (11 kV grid) -> MV bus -> TR-1 (2000 kVA) -> LV-1 -> feeders psu-a (fabrication line)
                                                                 and psu-b (utilities)
                                   -> TR-2 (1600 kVA) -> LV-2 -> feeder psu-c (assembly line)
  compressor-1 on psu-b, with the loop pump and the cooling of cool-1.
  cool-1: the loop pump, the process cooling, the compressor's oil and air coolers as one branch,
  press-1's oil cooler, and a heat branch for the machines still to come (a placeholder).
  The process cooling is an induced-draft cooling tower with a plate heat exchanger (the
  operator's conventional choice, 2026-10-08; cooling="tower", the default). The air-cooled chiller
  with its tempering valve stays as an option (cooling="chiller"), for precision cooling studies.

Each tick, in this order:
  1. the network solves with the loads of the last tick (power.py)
  2. each machine steps with its bus voltage and the water its branch got in the last loop step
  3. the loop steps: each branch returns the heat its machine gave the water in step 2
Placeholders (marked): the machine heat branches and the loop pump drive (a fixed efficiency,
until the pump motor gets its own datasheet).
"""
import math

from . import catalog as C
from .chiller import AirCooledChiller
from .compressor import P_ATM, ScrewCompressor
from .cooling_tower import TowerSystem
from .furnace import Furnace
from .gas import GasSupply
from .cnc import BoringMill
from .mech_press import MechanicalPress
from .conveyor import Conveyor
from . import fab_stations as FS
from . import assembly as AS
from .cooling import Branch, CoolingLoop
from .power import Bus, Cable, Grid, MotorLoad, Network, Transformer
from .press_brake import PressBrake
from .pump import Pump, PumpRating

# The loop pump: 60 m3/h at 25 m (project choice, sized for the loop's design flow at a 5 K rise
# on the chiller and the machines' branches). Its drive: 90 % motor efficiency, PF 0.85
# (PLACEHOLDER until a pump motor datasheet).
LOOP_PUMP = PumpRating(q_m3h=60.0, h_m=25.0, eta=0.72, n_rpm=2900.0, h0_m=32.0,
                       source="project choice: process loop pump")


class PumpDrive:
    """PLACEHOLDER electrical load of the loop pump: P = P_shaft / 0.9, PF 0.85."""
    def __init__(self, pump):
        self.pump = pump

    def s_va(self):
        p = self.pump.p_shaft / 0.9
        return complex(p, p * math.tan(math.acos(0.85)))


class ChillerLoad:
    def __init__(self, ch):
        self.ch = ch

    def s_va(self):
        p = self.ch.p_in
        return complex(p, p * math.tan(math.acos(self.ch.r.pf)))


class TowerLoads:
    """PLACEHOLDER electrical load of the tower: the fans (rated kW when on) and the tower pump
    (68 m3/h at about 15 m, 75 % wire-to-water: about 3.7 kW), PF 0.85."""
    def __init__(self, ts):
        self.ts = ts

    def s_va(self):
        p = 1000.0 * self.ts.tower.fan_kw() + (3.7e3 if self.ts.running else 0.0)
        return complex(p, p * math.tan(math.acos(0.85)))


class BlowerLoad:
    """PLACEHOLDER electrical load of the furnace's combustion air blower, PF 0.85."""
    def __init__(self, f):
        self.f = f

    def s_va(self):
        p = 1000.0 * self.f.blower_kw()
        return complex(p, p * math.tan(math.acos(0.85)))


class KwLoad:
    """An electrical load from a model's real power (attribute in W, or kW with scale 1000) and a PF."""
    def __init__(self, obj, attr="p_el", scale=1.0, pf=0.85):
        self.obj, self.attr, self.scale, self.pf = obj, attr, scale, pf

    def s_va(self):
        p = getattr(self.obj, self.attr) * self.scale
        return complex(p, p * math.tan(math.acos(self.pf)))


class UtilitiesPlant:
    def __init__(self, machine_heat_w=40e3, air_demand_frac=0.5, t_amb_c=35.0, with_press=True,
                 cooling="tower", t_wb_c=None, with_furnace=True, with_fab=True, with_assembly=True):
        mv = Bus("mv-11kv", 11000.0)
        self.grid = Grid("incomer-1", mv, C.GRID_11KV)
        self.net = Network(self.grid)
        lv1, lv2 = Bus("lv-1", 433.0), Bus("lv-2", 433.0)
        self.tr1 = self.net.add(Transformer("tr-1", mv, lv1, C.TR_2000KVA))
        self.tr2 = self.net.add(Transformer("tr-2", mv, lv2, C.TR_1600KVA))
        self.psu = {n: Bus(n, 433.0) for n in ("psu-a", "psu-b", "psu-c")}
        # feeder lengths: project choices (MCC rooms 40 to 80 m from the substation)
        self.net.add(Cable("feeder-a", lv1, self.psu["psu-a"], C.CABLE_LV_MAIN, 60.0, runs=2))
        self.net.add(Cable("feeder-b", lv1, self.psu["psu-b"], C.CABLE_LV_MAIN, 40.0, runs=1))
        self.net.add(Cable("feeder-c", lv2, self.psu["psu-c"], C.CABLE_LV_MAIN, 80.0, runs=2))
        for tr in (self.tr1, self.tr2):
            tr.theta_amb = t_amb_c
        # utilities
        self.comp = ScrewCompressor("compressor-1", C.GA37W_7_5, C.ABB_37KW, t_amb_c=t_amb_c)
        self.air_demand_frac = air_demand_frac
        self.comp.demands.append(lambda p: self.air_demand_frac * C.GA37W_7_5.fad_m3min / 60.0
                                 * min(1.0, max(0.0, (p - P_ATM) / 5.0)))
        self.comp.leak_m3s_bar = 0.20 * C.GA37W_7_5.fad_m3min / 60.0 / (P_ATM + 7.0)   # DOE: 20 %
        self.cooling = cooling
        t0 = 27.0
        if cooling == "chiller":
            self.chiller = AirCooledChiller("chiller-1", C.EWAD190AJYNN, t_amb_c=t_amb_c)
            cooler = self.chiller
        else:
            self.chiller = None
            self.tower = TowerSystem("cool-1-tower", C.EVAPCO_AT_14_99, C.PHE_COOL1,
                                     t_wb=C.SITE_WET_BULB_C if t_wb_c is None else t_wb_c)
            cooler, t0 = self.tower, 31.0
        # Resistances (project choices) put the pump at its design point (60 m3/h at 25 m:
        # k_total = 9.0e4 m/(m3/s)^2) and give compressor-1 its rated 0.9 kg/s: the branch flows
        # go as 1/sqrt(k), so k_comp / k_mach = (57 / 3.2)^2.
        self.loop = CoolingLoop("cool-1", Pump("cool-1-pump", LOOP_PUMP), volume_m3=4.0,
                                k_series=4.0e4, t0=t0)
        self.loop.chiller = cooler
        self.b_comp = self.loop.add(Branch("compressor-1", 1.77e7, heat=lambda t, m: self.comp.q_water))
        self.machine_heat_w = machine_heat_w      # PLACEHOLDER for the line machines
        # the machines without their own branch yet (placeholder) + cnc-1's spindle cooler + hot-1's
        # engine coolant through its cell heat exchanger
        self.b_mach = self.loop.add(Branch("machines", 5.58e4, heat=lambda t, m: self.machine_heat_w
                                           + (self.cnc.heat_w if getattr(self, "fab", False) else 0.0)
                                           + (self.hot.q_coolant if getattr(self, "asm", False) else 0.0)))
        b = self.psu["psu-b"]
        b.loads += [MotorLoad(self.comp.motor), PumpDrive(self.loop.pump),
                    ChillerLoad(self.chiller) if self.chiller else TowerLoads(self.tower)]
        # press-1 on psu-a (the fabrication line); its oil cooler is a loop branch whose
        # thermostatic valve opens and closes the branch (0.6 kg/s when open: k from the
        # compressor branch, flow ~ 1/sqrt(k))
        self.press = None
        if with_press:
            self.press = PressBrake("press-1", C.PPEB_320_40, C.ABB_37KW)
            self.press.motor.w = self.press.w_n
            self.psu["psu-a"].loads.append(MotorLoad(self.press.motor))
            self.b_press = self.loop.add(Branch("press-1", 3.8e7, heat=lambda t, m: self.press.q_water,
                                                valve=0.0))
        # fuel gas and furnace-1 (its blower on psu-a)
        self.gas = GasSupply("gas-1", C.GAS_SUPPLY)
        self.furnace = None
        if with_furnace:
            self.furnace = Furnace("furnace-1", C.CAR_BOTTOM_SR, self.gas, t_amb_c=t_amb_c)
            self.furnace.start()
            self.psu["psu-a"].loads.append(BlowerLoad(self.furnace))
        # the rest of the fabrication line (ideas.md 11.5)
        self.fab = with_fab
        if with_fab:
            a, b = self.psu["psu-a"], self.psu["psu-b"]
            self.cnc = BoringMill("cnc-1", C.DBC_130)
            self.press2 = MechanicalPress("press-2", C.AIDA_NC1_2000, C.YH2_160L_4)
            self.press2.motor.w = self.press2.w_m_n
            a.loads.append(MotorLoad(self.press2.motor))
            self.conv = Conveyor("conveyor-1", C.ROLLER_CONVEYOR)
            self.plasma = FS.PlasmaCutter("plasma-1", C.XPR300)
            self.welders = [FS.WeldingCell(f"weld-{k}", C.TPS500I, seed=k, change_s=240.0 + 37.0 * k)
                            for k in (1, 2)]
            self.scanner = FS.QaScanner("qa-scanner-1", C.SCANNER)
            self.blast = FS.ShotBlast("blast-1", C.RRB_16_5)
            self.paint = FS.PaintBooth("paint-1", C.HEAVY_BOOTH, self.gas, t_amb=t_amb_c)
            a.loads += [KwLoad(self.cnc, scale=1000.0), KwLoad(self.conv, pf=0.80),
                        KwLoad(self.plasma, pf=0.98), KwLoad(self.scanner, pf=0.94)]
            a.loads += [KwLoad(w, pf=0.99) for w in self.welders]
            b.loads += [KwLoad(self.blast), KwLoad(self.paint)]
            # the plasma shield air comes from the plant air header (118 slpm while cutting)
            self.comp.demands.append(lambda p: 118.0 / 60000.0 if self.plasma.state == "cut" else 0.0)
            self._next_move = 300.0
        # the engine assembly line on TR-2 (ideas.md 11.6)
        self.asm = with_assembly
        if with_assembly:
            c = self.psu["psu-c"]
            self.pallet = AS.PalletConveyor("pallet-1", C.TS5_LINE)
            self.tight = [AS.TighteningStation(f"tight-{k}", C.NUTRUNNER, seed=k) for k in (1, 2)]
            self.washer = AS.PartsWasher("washer-1", C.MECWASH, t_amb=t_amb_c)
            self.leak = AS.LeakTester("leak-1", C.LEAK_TEST, t_room=t_amb_c)
            self.cold = AS.ColdTest("cold-1", C.QSL9_F85)
            self.hot = AS.HotTest("hot-1", C.QSL9_F85)
            c.loads += [KwLoad(self.pallet), KwLoad(self.washer), KwLoad(self.cold, pf=0.9),
                        KwLoad(self.hot, pf=0.95)]
            c.loads += [KwLoad(s, pf=0.95) for s in self.tight]
            self._washed = 0
        self.t = 0.0

    def step(self, dt):
        self.net.step(dt)
        vb = self.psu["psu-b"].v_ll
        m_comp = self.b_comp.q_m3s * 997.0
        self.comp.step(dt, vb, self.grid.f_hz, self.loop.t_supply, m_comp)
        if self.chiller:
            self.chiller.v = vb
        if self.press is not None:
            m_p = self.b_press.q_m3s * 997.0
            self.press.step(dt, self.psu["psu-a"].v_ll, self.grid.f_hz, self.loop.t_supply,
                            m_p if self.press.valve_open else 0.0)
            self.b_press.valve = 1.0 if self.press.valve_open else 0.0
        self.gas.step(dt)
        if self.furnace is not None:
            self.furnace.step(dt)
        if self.fab:
            va = self.psu["psu-a"]
            self.cnc.step(dt, va.v_ll)
            self.conv.step(dt, va.v_ll)
            if self.t >= self._next_move:                    # a weldment moves every 10 min
                self.conv.send(4000.0)
                self._next_move += 600.0
            self.plasma.air_bar = self.comp.p_rec - P_ATM
            self.press2.air_bar = self.comp.p_rec - P_ATM        # clutch air from the plant header
            self.press2.step(dt, va.v_ll, self.grid.f_hz)
            self.plasma.step(dt)
            for w in self.welders:
                w.step(dt)
            self.scanner.step(dt, va.v_pu)
            self.blast.step(dt)
            self.paint.step(dt)
        if self.asm:
            vc = self.psu["psu-c"]
            self.pallet.step(dt)
            for st in self.tight:
                st.step(dt, vc.v_pu)
            self.washer.step(dt)
            if self.washer.parts != self._washed:              # each washed block is leak-tested
                self._washed = self.washer.parts
                self.leak.air_bar = self.comp.p_rec - P_ATM
                # the block cools on its way: two stations of transfer (580 s) with blow-off air halve
                # its excess temperature (project choice; real lines cool the block before the test)
                t_room = self.leak.t_room
                t_arr = t_room + (self.washer.t_part_out - t_room) * 0.5
                self.leak.test(t_arr)
            self.cold.step(dt)
            self.hot.step(dt)
        self.loop.step(dt)
        self.t += dt

    def check(self):
        out = self.net.check() + self.comp.check() + self.loop.check() + self.loop.chiller.check()
        out += self.press.check() if self.press is not None else []
        out += self.furnace.check() if self.furnace is not None else []
        if self.fab:
            out += self.cnc.check() + self.conv.check() + self.press2.check()
        return out

    def tags(self):
        out = {f"{n}.voltage_v": b.v_ll for n, b in self.psu.items()}
        out.update({f"compressor-1.{k}": v for k, v in self.comp.tags().items()})
        if self.chiller:
            out.update({"chiller-1.power_kw": self.chiller.p_in / 1000.0,
                        "chiller-1.current_a": self.chiller.amps, "chiller-1.lwt_c": self.chiller.lwt})
        else:
            t = self.tower.tower
            out.update({"cool-1-tower.basin_c": t.t_basin, "cool-1-tower.hot_c": t.t_hot,
                        "cool-1-tower.fan_on": float(t.fan_on), "cool-1-tower.evap_m3h": t.evap_m3h})
        out.update({"cool-1.supply_c": self.loop.t_supply,
                    "cool-1.return_c": self.loop.t_return, "cool-1.flow_m3h": self.loop.q_m3s * 3600.0,
                    "tr-1.load_pct": self.tr1.k * 100.0, "tr-1.oil_c": self.tr1.theta_o,
                    "incomer-1.p_kw": self.grid.bus.s_down.real / 1000.0})
        if self.press is not None:
            out.update({f"press-1.{k}": v for k, v in self.press.tags().items()})
        if self.furnace is not None:
            out.update({f"furnace-1.{k}": v for k, v in self.furnace.tags().items()})
        out["gas-1.header_kpa"] = self.gas.p_out
        if self.fab:
            for m in (self.cnc, self.press2, self.conv, self.plasma, self.scanner, self.blast, self.paint,
                      *self.welders):
                out.update({f"{m.name}.{k}": v for k, v in m.tags().items()})
        return out
