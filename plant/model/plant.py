"""The plant (ideas.md 11.4 to 11.7): the supply, the shared utilities, the truck frame line, the
engine assembly line and the building, on one site.

Wiring (ideas.md 11.4, 11.7 and 17.5):
  incomer-1 (11 kV grid) -> MV bus -> TR-1 -> LV-1 -> feeders psu-a (the frame line, its cranes and
                                                      fume extractors) and psu-b (utilities, the CED
                                                      line and the frame bay services)
                                   -> TR-2 -> LV-2 -> feeders psu-c (engine assembly line) and psu-d
                                                      (the assembly bay services and the office block)
  The site is one shop with two bays and an office block (operator, 2026-10-15). The building
  services (building.py) are part of the site load. A plant does not export: check() flags reverse
  power at the incomer.
  The frame line (operator, 2026-10-15: a riveted heavy-truck ladder frame, frame_line.py): plasma-1,
  press-1 (cross member bends), press-2 (gussets), weld-1 and weld-2 (welded cross members), pierce-1
  (side member holes, the bottleneck), lmp-1 (the 5,000 t long member press), blast-1, ced-1 (the
  CED paint line), rivet-1, qa-scanner-1, conveyor-1. The stress-relief furnace and the boring mill
  left the plant with the boom product (their models stay in furnace.py and cnc.py as parts).
  compressor-1 on psu-b, with the loop pump and the cooling of cool-1.
  cool-1: the loop pump, the process cooling, the compressor's oil and air coolers as one branch,
  the oil coolers of press-1 and lmp-1 (thermostatic valves), and the machines branch (hot-1's
  engine coolant through its cell heat exchanger). `machine_heat_w` adds a heat load there for
  stress tests (default 0: no placeholder heat in a plant run).
  The process cooling is an induced-draft cooling tower with a plate heat exchanger (cooling="tower",
  the default). chiller-1 holds the CED bath (its tempering valve holds the bath circulation at
  29 C). cooling="chiller" puts an air-cooled chiller on cool-1 instead (precision cooling studies);
  the CED bath then has its own chiller-2.
  Weather: weather="fixed" (default, the test condition: 35 C dry bulb everywhere and the 1 % wet
  bulb) or weather="site" (weather.py: the hourly site weather of `day_of_year`, else of the seed's
  day of the year; the
  shop air is the air washers' leaving air when they run: DB - 0.85 (DB - WB), a saturation
  efficiency inside the 70 to 95 % of direct evaporative coolers, project choice).
  Material flow (flow.py): with flow=True (default) each station starts when its part arrives and an
  operator has handled it; flow=False keeps the old free-running clocks (the station tests).

Each tick, in this order:
  1. the network solves with the loads of the last tick (power.py)
  2. the weather and the flow controllers act
  3. each machine steps with its bus voltage and the water its branch got in the last loop step
  4. the loop steps: each branch returns the heat its machine gave the water in step 3
Placeholders (marked): the electrical loads of the loop pump and the tower (fixed efficiencies,
until their motor datasheets), and the plant air demand of the users without their own model.
"""
import math
import random

from . import building as B
from . import catalog as C
from .chiller import AirCooledChiller
from .compressor import P_ATM, ScrewCompressor
from .cooling_tower import TowerSystem
from .gas import GasSupply
from .mech_press import MechanicalPress
from .conveyor import Conveyor
from . import fab_stations as FS
from . import assembly as AS
from . import frame_line as FL
from .cooling import Branch, CoolingLoop
from .flow import EngineFlow, FrameFlow
from .power import Apfc, Bus, Cable, Grid, MotorLoad, Network, Transformer
from .press_brake import Plate, PressBrake
from .pump import Pump, PumpRating
from .weather import FixedWeather, site_weather

# The loop pump: 60 m3/h at 25 m (project choice, sized for the loop's design flow at a 5 K rise
# on the chiller and the machines' branches). Its drive: 90 % motor efficiency, PF 0.85
# (PLACEHOLDER until a pump motor datasheet).
LOOP_PUMP = PumpRating(q_m3h=60.0, h_m=25.0, eta=0.72, n_rpm=2900.0, h0_m=32.0,
                       source="project choice: process loop pump")

AIR_WASHER_EFF = 0.85


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


class KwLoad:
    """An electrical load from a model's real power (attribute in W, or kW with scale 1000) and a PF.
    The reactive power is lagging in both directions: a drive that feeds power back (hot-1's dyno)
    still draws its magnetising and filter reactive power, it does not supply it (fixed 2026-10-15:
    Q = P tan(phi) made the regenerating dyno a capacitor)."""
    def __init__(self, obj, attr="p_el", scale=1.0, pf=0.85):
        self.obj, self.attr, self.scale, self.pf = obj, attr, scale, pf

    def s_va(self):
        p = getattr(self.obj, self.attr) * self.scale
        if self.__dict__.get("_tan_pf", (None,))[0] != self.pf:     # tan(acos(pf)) once per pf
            self._tan_pf = (self.pf, math.tan(math.acos(self.pf)))
        return complex(p, abs(p) * self._tan_pf[1])


class SvaLoad:
    """A model that gives its own complex power (s_va)."""
    def __init__(self, obj):
        self.obj = obj

    def s_va(self):
        return self.obj.s_va()


class UtilitiesPlant:
    # Part cooling station before the leak test. Design rule (2026-10-15, verification pass 2: at
    # 170 s the healthy line rejected half its engines on a cool day): the thermal decay at most half
    # the 42 Pa reject limit (0.75 K at about 28 Pa/K) at the coldest shop air of the site year (5.8 C)
    # for a block that leaves the washer at 65 C: 580 s / ln(59.2 / 0.75) = 133 s -> 130 s.
    PART_COOL_TAU_S = 130.0
    EXPORT_MARGIN_W = 20e3      # the export limiter keeps this much import (project choice)
    CED_BATH_SETPOINT_C = 29.0  # the chiller's tempering valve holds the bath circulation here (project choice)

    def __init__(self, machine_heat_w=0.0, air_demand_frac=0.5, t_amb_c=35.0, with_press=True,
                 cooling="tower", t_wb_c=None, with_fab=True, with_assembly=True,
                 with_building=True, t_day0_h=8.0, seed=0, material_spread=True, tr_fab=None, tr_asm=None,
                 with_apfc=True, weather="fixed", flow=True, day_of_year=None):
        mv = Bus("mv-11kv", 11000.0)
        self.grid = Grid("incomer-1", mv, C.GRID_11KV)
        self.net = Network(self.grid)
        lv1, lv2 = Bus("lv-1", 433.0), Bus("lv-2", 433.0)
        self.tr1 = self.net.add(Transformer("tr-1", mv, lv1, tr_fab or C.TR_FAB))
        self.tr2 = self.net.add(Transformer("tr-2", mv, lv2, tr_asm or C.TR_ASM))
        self.psu = {n: Bus(n, 433.0) for n in ("psu-a", "psu-b", "psu-c", "psu-d")}
        # feeder lengths: project choices (MCC rooms 40 to 80 m from the substation); feeder-b has two
        # runs since it also carries the frame bay services (2026-10-15)
        self.net.add(Cable("feeder-a", lv1, self.psu["psu-a"], C.CABLE_LV_MAIN, 60.0, runs=2))
        self.net.add(Cable("feeder-b", lv1, self.psu["psu-b"], C.CABLE_LV_MAIN, 40.0, runs=2))
        self.net.add(Cable("feeder-c", lv2, self.psu["psu-c"], C.CABLE_LV_MAIN, 80.0, runs=2))
        self.net.add(Cable("feeder-d", lv2, self.psu["psu-d"], C.CABLE_LV_MAIN, 30.0, runs=1))
        # APFC panels on the LV mains (2026-10-15): a factory corrects its power factor
        self.apfc = [Apfc("apfc-1", lv1, C.APFC_LV1), Apfc("apfc-2", lv2, C.APFC_LV2)] if with_apfc else []
        # weather
        if weather == "site":
            self.wx = site_weather(seed=seed, t_day0_h=t_day0_h, day0=day_of_year)
            self.site_weather = not isinstance(self.wx, FixedWeather)
        else:
            self.wx = FixedWeather(t_db=t_amb_c, t_wb=C.SITE_WET_BULB_C if t_wb_c is None else t_wb_c)
            self.site_weather = False
        self.t_amb = self.t_shop = self.wx.at(0.0).t_db
        self.t_day0_h = t_day0_h
        self.seed = seed
        self.rng = random.Random(f"plant:{seed}")
        self.material_spread = material_spread
        for tr in (self.tr1, self.tr2):
            tr.theta_amb = self.t_amb
        # utilities
        self.comp = ScrewCompressor("compressor-1", C.GA37W_7_5, C.ABB_37KW, t_amb_c=self.t_amb)
        self.air_demand_frac = air_demand_frac       # PLACEHOLDER: the air users without a model
        self.comp.demands.append(lambda p: self.air_demand_frac * C.GA37W_7_5.fad_m3min / 60.0
                                 * min(1.0, max(0.0, (p - P_ATM) / 5.0)))
        self.comp.leak_m3s_bar = 0.20 * C.GA37W_7_5.fad_m3min / 60.0 / (P_ATM + 7.0)   # DOE: 20 %
        self._leak_healthy = self.comp.leak_m3s_bar
        self.cooling = cooling
        t0 = 27.0
        if cooling == "chiller":
            self.chiller = AirCooledChiller("chiller-1", C.EWAD190AJYNN, t_amb_c=self.t_amb)
            cooler = self.chiller
        else:
            self.chiller = None
            self.tower = TowerSystem("cool-1-tower", C.EVAPCO_AT_14_99, C.PHE_COOL1,
                                     t_wb=self.wx.at(0.0).t_wb)
            cooler, t0 = self.tower, 31.0
        # Resistances (project choices) put the pump at its design point (60 m3/h at 25 m:
        # k_total = 9.0e4 m/(m3/s)^2) and give compressor-1 its rated 0.9 kg/s: the branch flows
        # go as 1/sqrt(k), so k_comp / k_mach = (57 / 3.2)^2.
        self.loop = CoolingLoop("cool-1", Pump("cool-1-pump", LOOP_PUMP), volume_m3=4.0,
                                k_series=4.0e4, t0=t0)
        self.loop.chiller = cooler
        self.b_comp = self.loop.add(Branch("compressor-1", 1.77e7, heat=lambda t, m: self.comp.q_water))
        self.machine_heat_w = machine_heat_w      # stress-test heat on the machines branch (default 0)
        self.b_mach = self.loop.add(Branch("machines", 5.58e4, heat=lambda t, m: self.machine_heat_w
                                           + (self.hot.q_coolant if getattr(self, "asm", False) else 0.0)))
        b = self.psu["psu-b"]
        b.loads += [MotorLoad(self.comp.motor), PumpDrive(self.loop.pump),
                    ChillerLoad(self.chiller) if self.chiller else TowerLoads(self.tower)]
        # press-1 on psu-a: bends the liners and cross members of the frames (8 mm, 2.5 m: project
        # choice inside the 10 mm frame plate of Press Kogyo); its oil cooler is a
        # loop branch whose thermostatic valve opens and closes it (0.6 kg/s when open)
        self.press = None
        if with_press:
            self.press = PressBrake("press-1", C.PPEB_320_40, C.ABB_37KW,
                                    plate=Plate(uts_mpa=510.0, t_mm=8.0, length_m=2.5, v_mm=80.0))
            self.press.motor.w = self.press.w_n
            self.psu["psu-a"].loads.append(MotorLoad(self.press.motor))
            self.b_press = self.loop.add(Branch("press-1", 3.8e7, heat=lambda t, m: self.press.q_water,
                                                valve=0.0))
        # fuel gas: the CED line's tank burners and cure oven
        self.gas = GasSupply("gas-1", C.GAS_SUPPLY)
        # the truck frame line (frame_line.py, ideas.md 11.5)
        self.fab = with_fab
        self._plate_override = None
        if with_fab:
            a, b = self.psu["psu-a"], self.psu["psu-b"]
            self.member = C.SIDE_MEMBER
            self.pierce = FL.PunchLine("pierce-1", C.PUNCH_450, C.ABB_M3BP_180MLB4_22KW, self.member, seed=seed)
            self.lmp = PressBrake("lmp-1", C.LMP_5000, C.ABB_160KW, n_motors=2, plate=Plate(**vars(C.SIDE_MEMBER_U)))
            self.lmp.motor.w = self.lmp.w_n
            self.lmp.auto = not flow
            self.b_lmp = self.loop.add(Branch("lmp-1", 3.8e7 * (0.6 / C.LMP_5000.water_kgs) ** 2,
                                              heat=lambda t, m: self.lmp.q_water, valve=0.0))
            self.press2 = MechanicalPress("press-2", C.AIDA_NC1_2000, C.YH2_160L_4, seed=seed)
            self.press2.motor.w = self.press2.w_m_n
            self.conv = Conveyor("conveyor-1", C.ROLLER_CONVEYOR)
            # the consumables have run for a while when the plant starts: any age, drawn per seed
            # (its own stream, so the other random draws of the plant stay the same)
            self.plasma = FS.PlasmaCutter("plasma-1", C.XPR300, cut_m_per_plate=12.0, pierces_per_plate=24,
                                          age=random.Random(f"consumables:{seed}").random())
            self.welders = [FS.WeldingCell(f"weld-{k}", C.TPS500I, seams=4, seed=k, change_s=240.0 + 37.0 * k)
                            for k in (1, 2)]
            self.scanner = FS.QaScanner("qa-scanner-1", C.SCANNER)
            self.blast = FS.ShotBlast("blast-1", C.RRB_16_5)
            if cooling == "chiller":
                self.ced_chiller = AirCooledChiller("chiller-2", C.EWAD190AJYNN,
                                                    process_setpoint_c=self.CED_BATH_SETPOINT_C, t_amb_c=self.t_amb)
            else:
                self.ced_chiller = AirCooledChiller("chiller-1", C.EWAD190AJYNN,
                                                    process_setpoint_c=self.CED_BATH_SETPOINT_C, t_amb_c=self.t_amb)
            self.ced = FL.CedLine("ced-1", C.CED_LINE, self.gas, self.ced_chiller, t_shop=self.t_amb)
            self.rivet = FL.RivetStation("rivet-1", C.RIVET, seed=seed)
            a.loads += [MotorLoad(self.press2.motor), SvaLoad(self.pierce), MotorLoad(self.lmp.motor, count=2),
                        KwLoad(self.conv, pf=0.80), KwLoad(self.plasma, pf=0.98), KwLoad(self.scanner, pf=0.94),
                        SvaLoad(self.rivet)]
            a.loads += [KwLoad(w, pf=0.99) for w in self.welders]
            b.loads += [KwLoad(self.blast), SvaLoad(self.ced), ChillerLoad(self.ced_chiller)]
            # the plasma shield air comes from the plant air header (118 slpm while cutting)
            self.comp.demands.append(lambda p: 118.0 / 60000.0 if self.plasma.state == "cut" else 0.0)
            self._next_move = 300.0
        # the engine assembly line on TR-2 (ideas.md 11.6)
        self.asm = with_assembly
        if with_assembly:
            c = self.psu["psu-c"]
            self.pallet = AS.PalletConveyor("pallet-1", C.TS5_LINE)
            self.tight = [AS.TighteningStation(f"tight-{k}", C.NUTRUNNER, seed=k) for k in (1, 2)]
            self.washer = AS.PartsWasher("washer-1", C.MECWASH, t_amb=self.t_amb)
            self.leak = AS.LeakTester("leak-1", C.LEAK_TEST, t_room=self.t_amb, seed=seed,
                                      part_spread=material_spread)
            self.cold = AS.ColdTest("cold-1", C.QSL9_F85, seed=seed, engine_spread=material_spread)
            self.hot = AS.HotTest("hot-1", C.QSL9_F85)
            # hot-1's regenerative dyno drive has an active front end, near unity power factor in both
            # directions (0.99: project choice); cold-1's drive has a diode front end (0.9)
            c.loads += [KwLoad(self.pallet), KwLoad(self.washer), KwLoad(self.cold, pf=0.9),
                        KwLoad(self.hot, pf=0.99)]
            c.loads += [KwLoad(s, pf=0.95) for s in self.tight]
            self._washed = 0
        # the building services (building.py, 2026-10-15): frame bay on TR-1, assembly bay and office on TR-2
        self.building = []
        self.cranes = []
        if with_building:
            a, b, d = self.psu["psu-a"], self.psu["psu-b"], self.psu["psu-d"]
            fab_v, asm_v = C.FAB_BAY_M2 * C.FAB_BAY_H_M, C.ASM_BAY_M2 * C.ASM_BAY_H_M
            self.light_fab = B.Lighting("lights-fab", C.LIGHT_FAB)
            self.light_asm = B.Lighting("lights-asm", C.LIGHT_ASM)
            self.light_office = B.Lighting("lights-office", C.LIGHT_OFFICE, schedule=B.office_hours)
            self.vent_fab = B.RoofExtractors("vent-fab", C.ROOF_FAN, fab_v, C.ACH_FAB)
            self.vent_asm = B.RoofExtractors("vent-asm", C.ROOF_FAN, asm_v, C.ACH_ASM)
            self.washers_fab = B.AirWashers("airwash-fab", C.AIR_WASHER, self.vent_fab.q_m3h)
            self.washers_asm = B.AirWashers("airwash-asm", C.AIR_WASHER, self.vent_asm.q_m3h)
            self.aircon = B.OfficeAircon("aircon-office", C.OFFICE)
            self.plug = B.PlugLoad("plug-office", C.OFFICE)
            self.cranes = [B.EotCrane(f"crane-{k}", C.EOT_15T, offset_s=180.0 * (k - 1), seed=seed) for k in (1, 2)]
            self.fume = ([B.FumeExtractor(f"fume-{w.name}", C.FUME_DF6, w) for w in self.welders]
                         if self.fab else [])
            b.loads += [self.light_fab, self.vent_fab, self.washers_fab]
            a.loads += self.cranes + self.fume
            d.loads += [self.light_asm, self.vent_asm, self.washers_asm, self.light_office, self.aircon, self.plug]
            self.building = [self.light_fab, self.light_asm, self.light_office, self.vent_fab, self.vent_asm,
                             self.washers_fab, self.washers_asm, self.aircon, self.plug, *self.cranes, *self.fume]
        # material flow (flow.py)
        self.flow = flow
        self.fflow = self.eflow = None
        if flow and with_fab:
            if not self.cranes:
                raise ValueError("the frame flow needs the bay cranes (with_building=True)")
            for st in (self.plasma, *self.welders, self.blast, self.scanner, *self.cranes):
                st.gated = True
            self.press.auto = False
            self.press2.auto = False
            self.fflow = FrameFlow(self, self.member, seed=seed)
        if flow and with_assembly:
            for st in (self.pallet, *self.tight, self.washer, self.cold, self.hot):
                st.gated = True
            self.eflow = EngineFlow(self, seed=seed)
        # material batches (2026-10-15): plate and blank properties change per batch, inside their
        # standards: S355J2 UTS 470 to 630 MPa (British Steel S355J2 sheet), thickness EN 10029 class A
        # (-0.5 / +1.2 mm for 8 to 15 mm). Uniform inside the limits, 40 bends or 200 blanks per batch:
        # project choices. material_spread=False keeps the nominal plate and blank.
        self._batch_p1 = self._batch_p2 = -1
        # healthy values of the parameters that the fault registry changes (faults.py)
        self._healthy = {"F7": None, "F10": self.gas.p_supply, "F15": None,
                         "F14": self.eflow.H_HOT if self.eflow else None}
        self.t = 0.0

    def t_day_h(self):
        return (self.t_day0_h + self.t / 3600.0) % 24.0

    def _material_batches(self):
        if self.press is not None and self._plate_override is not None:
            self.press.plate.uts_mpa = self._plate_override
        if not self.material_spread:
            return
        if self.press is not None and self.press.bends // 40 != self._batch_p1:
            self._batch_p1 = self.press.bends // 40
            pl = self.press.plate
            pl.uts_mpa = self._plate_override or self.rng.uniform(470.0, 630.0)
            pl.t_mm = 8.0 + self.rng.uniform(-0.5, 1.2)
        if self.fab and self.press2.strokes // 200 != self._batch_p2:
            self._batch_p2 = self.press2.strokes // 200
            bl = self.press2.blank
            bl.shear_mpa = 0.8 * self.rng.uniform(470.0, 630.0)    # shear about 0.8 x UTS (as Blank)
            bl.t_mm = 10.0 + self.rng.uniform(-0.5, 1.2)

    def _weather(self):
        w = self.wx.at(self.t)
        self.t_amb = w.t_db
        if not self.site_weather:
            return
        on = self.building and self.washers_fab.on
        self.t_shop = w.t_db - AIR_WASHER_EFF * (w.t_db - w.t_wb) if on else w.t_db
        for tr in (self.tr1, self.tr2):
            tr.theta_amb = w.t_db
        if self.chiller:
            self.chiller.t_amb = w.t_db
        else:
            self.tower.tower.t_wb = w.t_wb
        self.comp.motor.t_cool_c = self.t_shop
        if self.fab:
            self.ced_chiller.t_amb = w.t_db
            self.ced.t_shop = self.t_shop
        if self.asm:
            self.washer.t_amb = self.t_shop
            self.leak.t_room = self.t_shop

    def _leak_test(self, t_part_out):
        """A washed block reaches the leak test (flow.py): two stations of transfer (580 s) through a
        part cooling station, exponential with PART_COOL_TAU_S (the design rule above). A pressure-decay
        test sees about 28 Pa per kelvin of part cooling here, and its reject limit is 42 Pa
        (12 scc/min), so a real line must bring the block within about 1 K of the room: a warm part
        reads as a leak (US 4,532,795; Cincinnati Test Systems)."""
        self.leak.air_bar = self.comp.p_rec - P_ATM
        t_room = self.leak.t_room
        t_arr = t_room + (t_part_out - t_room) * math.exp(-580.0 / self.PART_COOL_TAU_S)
        self.leak.test(t_arr)

    def step(self, dt):
        self.net.step(dt)
        for cb in self.apfc:
            cb.step(dt)
        self._weather()
        self._material_batches()
        if self.fflow:
            self.fflow.step(dt)
        if self.eflow:
            self.eflow.step(dt)
        if self.building:
            h = self.t_day_h()
            for ld in self.building:
                if isinstance(ld, (B.AirWashers, B.OfficeAircon)):
                    ld.step(dt, h, self.t_amb)
                else:
                    ld.step(dt, h)
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
        if self.fab:
            va = self.psu["psu-a"]
            self.pierce.step(dt, va.v_ll, self.grid.f_hz)
            m_l = self.b_lmp.q_m3s * 997.0
            self.lmp.step(dt, va.v_ll, self.grid.f_hz, self.loop.t_supply, m_l if self.lmp.valve_open else 0.0)
            self.b_lmp.valve = 1.0 if self.lmp.valve_open else 0.0
            self.conv.step(dt, va.v_ll)
            if not self.flow and self.t >= self._next_move:      # free-running: a move every 10 min
                self.conv.send(4000.0)
                self._next_move += 600.0
            self.plasma.air_bar = self.comp.p_rec - P_ATM
            self.press2.air_bar = self.comp.p_rec - P_ATM        # clutch air from the plant air header
            self.press2.step(dt, va.v_ll, self.grid.f_hz)
            self.plasma.step(dt)
            for w in self.welders:
                w.step(dt)
            self.scanner.step(dt, va.v_pu)
            self.blast.step(dt)
            self.ced_chiller.v = vb
            self.ced.step(dt)
            self.rivet.step(dt)
        if self.asm:
            vc = self.psu["psu-c"]
            self.pallet.step(dt)
            for st in self.tight:
                st.step(dt, vc.v_pu)
            self.washer.step(dt)
            if not self.flow and self.washer.parts != self._washed:   # free-running: each washed block
                self._washed = self.washer.parts
                self._leak_test(self.washer.t_part_out)
            self.cold.step(dt)
            # export limiter: keep at least EXPORT_MARGIN_W of import at the incomer (last tick's flow)
            others = self.grid.bus.s_down.real - self.hot.p_el
            self.hot.regen_cap_w = others + 5000.0 - self.EXPORT_MARGIN_W
            self.hot.step(dt)
        self.loop.step(dt)
        self.t += dt

    def check(self):
        out = self.net.check() + self.comp.check() + self.loop.check() + self.loop.chiller.check()
        out += self.press.check() if self.press is not None else []
        if self.fab:
            out += self.conv.check() + self.press2.check() + self.pierce.check() + self.lmp.check()
            out += self.ced.check() + self.ced_chiller.check()
        # A factory does not export: no export permit, and a reverse-power relay (ANSI 32) at the
        # incomer would trip. Power that the hot test feeds back must stay inside the site.
        if self.grid.bus.s_down.real < 0.0:
            out.append(f"incomer-1: the site exports {-self.grid.bus.s_down.real / 1000.0:.1f} kW")
        return out

    def tags(self):
        out = {f"{n}.voltage_v": b.v_ll for n, b in self.psu.items()}
        out.update({f"compressor-1.{k}": v for k, v in self.comp.tags().items()})
        chillers = [c for c in (self.chiller, getattr(self, "ced_chiller", None)) if c is not None]
        for ch in chillers:
            out.update({f"{ch.name}.power_kw": ch.p_in / 1000.0, f"{ch.name}.current_a": ch.amps,
                        f"{ch.name}.lwt_c": ch.lwt})
        if not self.chiller:
            t = self.tower.tower
            out.update({"cool-1-tower.basin_c": t.t_basin, "cool-1-tower.hot_c": t.t_hot,
                        "cool-1-tower.fan_on": float(t.fan_on), "cool-1-tower.evap_m3h": t.evap_m3h})
        s_in = self.grid.bus.s_down
        out.update({"cool-1.supply_c": self.loop.t_supply,
                    "cool-1.return_c": self.loop.t_return, "cool-1.flow_m3h": self.loop.q_m3s * 3600.0,
                    "tr-1.load_pct": self.tr1.k * 100.0, "tr-1.oil_c": self.tr1.theta_o,
                    "tr-2.load_pct": self.tr2.k * 100.0, "tr-2.oil_c": self.tr2.theta_o,
                    "incomer-1.p_kw": s_in.real / 1000.0, "incomer-1.q_kvar": s_in.imag / 1000.0,
                    "incomer-1.pf": s_in.real / abs(s_in) if abs(s_in) > 0 else 1.0})
        if self.site_weather:
            w = self.wx.at(self.t)
            out.update({"site.dry_bulb_c": w.t_db, "site.wet_bulb_c": w.t_wb, "site.rh_pct": w.rh,
                        "site.shop_air_c": self.t_shop})
        for ld in self.building + self.apfc:
            out.update({f"{ld.name}.{k}": v for k, v in ld.tags().items()})
        if self.asm:
            for m in (self.pallet, *self.tight, self.washer, self.leak, self.cold, self.hot):
                out.update({f"{m.name}.{k}": v for k, v in m.tags().items()})
        if self.press is not None:
            out.update({f"press-1.{k}": v for k, v in self.press.tags().items()})
        out["gas-1.header_kpa"] = self.gas.p_out
        if self.fab:
            for m in (self.pierce, self.lmp, self.press2, self.conv, self.plasma, self.scanner, self.blast,
                      self.ced, self.rivet, *self.welders):
                out.update({f"{m.name}.{k}": v for k, v in m.tags().items()})
        return out
