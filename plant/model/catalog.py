"""Reference parts from public datasheets. Each entry names its source. Local copies of the
documents are in references/ (gitignored). plant/model/README.md lists the URLs."""
from .motor import MotorRating

# ABB M3BP 180MLB 4, 22 kW, 4-pole, 400 V, 50 Hz, IE3, IC411 TEFC, IEC 60034-12 design N.
# ABB technical data sheet, DOL, product code 3GBP182420-ADC (EMM18224-PPN), issued 2015-12-23:
# https://www.baldor.com/api/products/EMM18224-PPN/drawings/EMM18224-PPN_TECH
# Efficiency per IEC 60034-2-1:2007 with PLL from residual loss. Insulation F, rise class B.
# Not on the sheet: the thermal split (project choices in MotorRating).
ABB_M3BP_180MLB4_22KW = MotorRating(
    p_kw=22.0, v_ll=400.0, f_hz=50.0, poles=4, n_rpm=1480.0, i_a=41.5,
    eta=0.933, pf=0.82, is_in=8.2, ts_tn=2.8, tmax_tn=3.1, j_kgm2=0.217,
    eta_75=0.941, eta_50=0.941, pf_75=0.78, pf_50=0.69, i0_a=16.6, pf_start=0.48,
    rise_k=80.0, t_amb_c=40.0, mass_kg=229.0, t_start_cold_s=27.0, t_start_hot_s=15.0,
    source="ABB data sheet 3GBP182420-ADC (EMM18224-PPN), 2015-12-23",
)

# ABB M3BP 225SMA 4, 37 kW, 4-pole, 400 V, 50 Hz, IE3, IC411 TEFC, design N (drives compressor-1).
# ABB technical data sheet, DOL, product code 3GBP222210-ADC (EMM22374-PPN), issued 2016-02-01:
# https://www.baldor.com/api/products/EMM22374-PPN/drawings/EMM22374-PPN_TECH
ABB_37KW = MotorRating(
    p_kw=37.0, v_ll=400.0, f_hz=50.0, poles=4, n_rpm=1482.0, i_a=65.4,
    eta=0.949, pf=0.86, is_in=7.7, ts_tn=2.8, tmax_tn=3.1, j_kgm2=0.536,
    eta_75=0.955, eta_50=0.954, pf_75=0.83, pf_50=0.75, i0_a=19.6, pf_start=0.40,
    rise_k=80.0, t_amb_c=40.0, mass_kg=376.0, t_start_cold_s=27.0, t_start_hot_s=15.0,
    source="ABB data sheet 3GBP222210-ADC (EMM22374-PPN), 2016-02-01",
)
ABB_37KW_CHECKS = {"i_75_a": 50.5, "i_50_a": 37.3, "tmin_tn": 2.3}

# Datasheet values that the fit does NOT use. The tests check the model against them.
ABB_M3BP_180MLB4_22KW_CHECKS = {
    "i_75_a": 32.5,            # current at 75 % load
    "i_50_a": 24.5,            # current at 50 % load
    "tmin_tn": 2.1,            # pull-up torque (not modelled: harmonic torques, see README)
    "t_start_max_hot_s": 15.0,   # maximum starting time from hot
    "t_start_max_cold_s": 27.0,  # maximum starting time from cold
    "insulation_limit_c": 155.0,  # thermal class F (IEC 60085)
}

# ------------------------------------------------------------------------------ power supply --
from .power import CableRating, GridRating, TransformerRating   # noqa: E402

# 11 kV utility supply. Fault level: the 11 kV system fault level of 500 MVA that the MSEDCL
# technical specification for 5 and 10 MVA power transformers (2019, clauses 2.15 and 2.16) uses
# as its design value. X/R = 10 is a project choice (no standard value found; forum range 5 to 15).
GRID_11KV = GridRating(v_ll=11000.0, fault_mva=500.0, x_r=10.0,
                       source="MSEDCL 5/10 MVA transformer spec 2019 (fault level); X/R project choice")

# Distribution transformers 11/0.433 kV, Dyn11, ONAN, IS 1180 (Part 1):2014 with Amendment 4 (2021),
# energy efficiency level 2, as summarised by BIS:
# https://www.bis.gov.in/wp-content/uploads/2022/04/Transformers-_compressed.pdf
# IS 1180 gives the total loss at 50 % and 100 % load; with total = P0 + k^2 Pk:
#   Pk = (T100 - T50) / 0.75,  P0 = T100 - Pk.
# Impedance 6.25 % at 75 C for 1600 and 2000 kVA. No-load current: IS 1180 allows at most 2 %;
# 1 % is a project choice. Temperature rise limits of IS 1180: top oil 40 K, winding 45 K.
# Hot-spot gradient H g_r: g_r = 45 K - average oil rise (0.8 x 40 K, project choice) = 13 K, H = 1.1
# (IEC 60076-7 for distribution transformers) -> 14.3 K. Time constants: oil 180 min, winding 4 min,
# x = 0.8, y = 1.6, k11 = 1, k21 = 1, k22 = 2 (IEC 60076-7 values for ONAN distribution transformers,
# as cited by arXiv 1906.01570; the standard's own table not seen).
_IS1180 = dict(v1_ll=11000.0, v2_ll=433.0, uk_pct=6.25, i0_pct=1.0, d_theta_or=40.0, h_gr=14.3,
               tau_o_min=180.0, tau_w_min=4.0)
TR_2000KVA = TransformerRating(kva=2000.0, p0_w=14100.0 - (14100.0 - 4790.0) / 0.75,
                               pk_w=(14100.0 - 4790.0) / 0.75,
                               source="IS 1180-1:2014 A4 level 2, 2000 kVA (BIS summary)", **_IS1180)
TR_1600KVA = TransformerRating(kva=1600.0, p0_w=11300.0 - (11300.0 - 3970.0) / 0.75,
                               pk_w=(11300.0 - 3970.0) / 0.75,
                               source="IS 1180-1:2014 A4 level 2, 1600 kVA (BIS summary)", **_IS1180)

# LV feeder cable: 1.1 kV XLPE armoured, 3.5 core, 300 mm2 aluminium. Polycab LT cable catalogue:
# AC resistance at 90 C 0.129 ohm/km, reactance 0.0703 ohm/km, 452 A in air (40 C):
# https://cms.polycab.com/media/cxibmmjk/final_lt-cable-catalog_v9_ord-10054.pdf
CABLE_LV_MAIN = CableRating(r_ohm_km=0.129, x_ohm_km=0.0703, i_rated_a=452.0,
                            source="Polycab LT XLPE catalogue, 3.5C 300 mm2 Al")

# Voltage dip test levels, IEC 61000-4-11 class 3 (as given by Kikusui and AMETEK test notes):
# (residual share of nominal, duration in cycles at 50 Hz)
DIPS_CLASS3 = ((0.0, 0.5), (0.0, 1.0), (0.40, 10.0), (0.70, 25.0), (0.80, 250.0))

# ------------------------------------------------------------------------------- compressor-1 --
from .compressor import CompressorRating   # noqa: E402

# Atlas Copco GA 37 W (water-cooled), 7.5 bar variant, 50 Hz. Sources:
#  - Instruction book GA30/37/45 (2920 1163 04), e-pneumatic copy: unloading 7.5 bar(e), nominal
#    working pressure 7.0 bar(e), electrical input 40.8 kW (water-cooled, Pack) at reference
#    conditions (1 bar(a), 20 C), safety valve 8.5 bar(e), star-delta changeover 10 s after the
#    start, oil by-pass valve opens at 40 C and sends all oil through the cooler at about 55 C,
#    cooling water outlet 50 C maximum:
#    https://www.e-pneumatic.com/media/pdf/atlas/atlas-copco-ga-45-pdf.pdf
#  - Leaflet GA30-90: FAD 116 l/s at 7 bar(e), ISO 1217 Annex C:
#    https://www.atlascopco.com/content/dam/atlas-copco/compressor-technique/industrial-air/documents/brochures/air-compressors/oil-lubricated/GA30-90_antwerp_leaflet_EN_2935089249.pdf
#  - CAGI sheet GA 37 water-cooled 100 psig: unloaded input 10.7 kW of 42.9 kW = 24.9 %.
#  - US DOE compressed air sourcebook v3: about 40 s for the sump to blow down (p. 39) -> a time
#    constant of 13 s (95 % in 40 s, derived).
#  - Kaeser CSD brochure: 76 % of the input to the oil cooler, 15 % to the aftercooler, 5 % in the
#    motor -> 0.80 and 0.16 of the shaft power (derived).
# Derived or project choices: shaft power 37.9 kW (the shaft power that gives the 40.8 kW input on
# the fitted 37 kW motor); oil flow 1.0 kg/s (gives the 70 to 75 C element outlet of the instruction book's
# example at the oil set point); oil set point 55 C (all oil through the cooler, per the book);
# cooling water 0.9 kg/s (about 10 K rise at full load, under the 50 C outlet limit); receiver 1.5 m3
# (Atlas Copco formula V = 0.25 Qc P1 / (fmax dP) with 116 l/s, 1 bar, 1 cycle per 30 s, 0.6 bar)
# plus 0.5 m3 of header pipework; load pressure 6.9 bar(e) (0.6 bar under unload); element shutdown
# 120 C (not given in the book: project choice); 6 starts per hour (Compressed Air Challenge: size
# for 6 to 8 starts per hour); auto stop after 10 min unloaded (project choice). Overload relay:
# the instruction book's maximum setting, 42 A in the phase (delta) circuit of the star-delta
# starter, is 72.7 A of line current.
GA37W_7_5 = CompressorRating(
    fad_m3min=116.0 * 0.06, p_work_barg=7.0, p_max_barg=7.5, p_shaft_kw=37.9,
    unloaded_frac=0.249, blowdown_s=13.0, n_poly=1.3, oil_heat_frac=0.80, air_heat_frac=0.16,
    oil_flow_kgs=1.0, oil_set_c=55.0, oil_mass_kg=25.0, t_trip_c=120.0,
    water_flow_kgs=0.9, water_in_c=28.0, starts_per_hour=6, star_delta_s=10.0,
    auto_stop_s=600.0, receiver_m3=2.0, safety_barg=8.5, load_barg=6.9, unload_barg=7.5,
    ol_set_a=42.0 * 3 ** 0.5,
    source="Atlas Copco GA 37 W 7.5 bar: instruction book 2920 1163 04, leaflet, CAGI")

# --------------------------------------------------------------------------------- chiller-1 --
from .chiller import ChillerRating   # noqa: E402

# Daikin EWAD190AJYNN, air-cooled, R-134a, 2 single-screw compressors on 2 circuits.
# Capacity (CC) and compressor input (PI, compressor only) by leaving water 4-15 C and ambient
# 25-44 C: Daikin EWAD190-280AJYNN cooling capacity tables,
# https://www.daikin.es/content/dam/MDM/TechnicalDrawings/AppliedSystems/EN/EWAD190-280AJYNN_capcool_EN.pdf
# Minimum capacity 12.5 %, max running current 178.2 A, PF 0.86 (IOM table 18); start-to-start
# 600 s for one compressor, minimum off 180 s (control panel manual D-EOMCP00104-14EN, 6.6).
# Part load at constant conditions: P / P_full = PLR (1 + 0.053 (1 - PLR)), the one parameter set
# so that the ESEER / EER ratio of the related EWAD-D-SR 190 (2.91 / 2.28) comes out with the
# ambient relief of this table (derived). Condenser fans: 6 kW (project choice: the unit-power
# difference between EWAD-D-SR 190 and the compressor input of this table). Evaporator flow at a
# 5 K design rise (project choice; the IOM gives a chart only).
_LWT = tuple(float(x) for x in range(4, 16))
_AMB = (25.0, 30.0, 35.0, 40.0, 44.0)
_CC = ((187.3, 178.2, 168.5, 158.3, 149.6), (192.9, 183.5, 173.6, 163.1, 154.3),
       (198.5, 188.8, 178.7, 168.1, 158.1), (204.2, 194.3, 184.0, 173.1, 160.1),
       (209.8, 200.0, 189.3, 178.2, 161.9), (215.5, 205.5, 194.7, 183.3, 163.5),
       (221.3, 211.1, 200.3, 188.5, 164.9), (227.1, 216.8, 205.8, 193.9, 166.1),
       (233.0, 222.5, 211.3, 199.4, 168.3), (239.0, 228.2, 216.9, 204.9, 169.2),
       (245.1, 234.1, 222.5, 206.6, 171.0), (251.4, 240.0, 228.2, 208.3, 171.6))
_PI = ((60.9, 66.9, 73.3, 80.3, 86.2), (61.9, 67.9, 74.4, 81.4, 87.4), (63.0, 69.0, 75.5, 82.6, 88.0),
       (64.1, 70.1, 76.7, 83.7, 86.5), (65.2, 71.3, 77.8, 84.9, 85.0), (66.3, 72.5, 79.1, 86.2, 83.4),
       (67.4, 73.6, 80.3, 87.4, 81.9), (68.6, 74.8, 81.6, 88.8, 80.3), (69.7, 76.1, 82.8, 90.1, 79.5),
       (71.0, 77.3, 84.1, 91.4, 77.9), (72.2, 78.6, 85.4, 90.2, 77.1), (73.5, 79.9, 86.8, 88.6, 75.4))
_PL = tuple((x, x * (1.0 + 0.053 * (1.0 - x))) for x in (0.125, 0.25, 0.5, 0.75, 1.0))
EWAD190AJYNN = ChillerRating(
    lwt_axis=_LWT, amb_axis=_AMB, cap_kw=_CC, pin_kw=_PI, part_load=_PL, min_load=0.125,
    flow_nominal_kgs=222.5 / (4.186 * 5.0), rla_a=178.2, pf=0.86, anti_recycle_s=600.0, min_off_s=180.0, fan_kw=6.0,
    source="Daikin EWAD190AJYNN capacity tables, IOM, control panel manual")

# ------------------------------------------------------------------------------- fuel gas --
from .gas import BurnerRating, GasSupplyRating   # noqa: E402

# Piped natural gas to the plant. Supply: industrial customers get 1.5 to 4 bar(g) through their
# metering and regulating station (HPCL natural gas page; PNGRB T4S: secondary network 100 mbar to
# 7 bar) -> 2.0 bar(g) here. Heating value: the PPAC basis GCV 10,000 kcal/SCM with NCV 90 % of GCV
# (BPCL natural gas conversions) -> LHV 37.7 MJ/SCM. Plant regulator: a pilot-operated regulator,
# accuracy class AC 2.5 (EN 334; Fiorentini Dixi flyer), set to 300 mbar for the burner trains
# (project choice); the droop spreads the 2.5 % band over the plant's peak gas flow (200 m3/h,
# project choice); minimum differential 0.5 bar (the Dixi's minimum inlet, project choice for the
# differential); wide-open resistance a project choice.
GAS_SUPPLY = GasSupplyRating(
    p_supply_kpa=200.0, p_set_kpa=30.0, droop_kpa_per_m3h=0.025 * 30.0 / 200.0, dp_min_kpa=50.0,
    k_open=0.002, lhv_mj_m3=10000.0 * 0.90 * 4.1868e-3,
    source="HPCL/PNGRB supply pressure, PPAC GCV basis, EN 334 AC 2.5 (Fiorentini Dixi)")
# Burner efficiency: available heat of natural gas with 10 % excess air at a 600 to 650 C flue is
# about 61 to 63 % (US DOE process heating tip sheet 2, Bennett chart). Low-gas-pressure switch at
# half the burner inlet pressure (Kromschroeder on EN 746-2 gas trains with tightness control).
BURNER_STRESS_RELIEF = dict(eff=0.61, source="DOE tip sheet 2 available heat; Kromschroeder low-gas switch")

# -------------------------------------------------------------------------------------- press-1 --
from .press_brake import PressBrakeRating   # noqa: E402

# LVD PPEB 320/40 hydraulic press brake: 3200 kN, 4000 mm, stroke 300 mm, approach 120 mm/s,
# working 14 mm/s, return 130 mm/s, main motor 37 kW, oil tank 400 l, maximum pressure 285 bar, two
# cylinders (LVD PPEB brochure 2021,
# https://www.lvdgroup.com/sites/default/files/uploads/downloads/PPEB_2021_EN.pdf). Cylinder area
# from force / pressure: 1123 cm2 (derived). Oil 35 to 60 C, not above 70 C (Durma maintenance
# notes) -> the trip at 70 C. Annulus 0.105 of the bore: the same pump flow gives 14 mm/s on the bore
# and 130 mm/s on the return (derived from the brochure speeds). Project choices: ram mass 4 t, pump
# volumetric efficiency 0.95 and hydraulic-mechanical 0.90, idle circulation 8 bar, a water-cooled
# oil cooler of 15 kW on cool-1 (LVD offers an air cooler), part handling 25 s per bend.
PPEB_320_40 = PressBrakeRating(
    force_kn=3200.0, p_max_bar=285.0, v_approach=120.0, v_bend=14.0, v_return=130.0, stroke_mm=300.0,
    tank_l=400.0, ram_mass_kg=4000.0, annulus_frac=0.105, trip_oil_c=70.0, oil_cooler_kw=15.0,
    water_kgs=0.6, source="LVD PPEB 320/40 brochure 2021; Durma oil temperature notes")

# ------------------------------------------------------------------------------------ furnace-1 --
from .furnace import FurnaceRating   # noqa: E402

# Gas-fired car-bottom stress-relieving furnace for excavator booms and arms. Sourced pattern:
# car-bottom furnaces of this kind run 150 to 900 C with several control zones and high-velocity
# burners (Nutec Bickley car-bottom article; Pyradia brochure), 4 zones and a ceramic-fibre lining
# with a skin at or under 80 C (BHEL tender 2621200030, 50 t gas-fired furnace).
# Project choices (no maker table found): 4 zones of 375 kW (1.5 MW installed) at 15 kPa burner inlet;
# 25 t of steel per batch plus 6 t of car and fixtures; 150 m2 of exposed load surface (box sections,
# about 0.6 of the plate area of 25 mm plate); 114 m2 of lining (a 3.5 x 8.5 x 3.5 m chamber); lining
# loss 0.65 W/m2 K (about 400 W/m2 at 650 C, a fibre lining with a cool skin); chamber heat capacity
# 3 MJ/K; convection 30 W/m2 K and effective emissivity 0.6 to the load; 2 kW/K between zones;
# combustion air blower 15 kW.
CAR_BOTTOM_SR = FurnaceRating(
    zones=4, burner_kw=375.0, burner_p_kpa=15.0, load_t=25.0, car_t=6.0, load_area_m2=150.0,
    wall_area_m2=114.0, u_wall=0.65, c_furnace_j_k=3.0e6, h_conv=30.0, eps=0.6, g_zone=2000.0,
    blower_kw=15.0, source="pattern: Nutec Bickley, Pyradia, BHEL tender; sizes project choices")

# ------------------------------------------------------------------- cool-1 cooling tower --
from .cooling_tower import TowerRating   # noqa: E402

# EVAPCO AT 14-99 induced-draft counterflow tower: 300 USgpm (68 m3/h) from 95 F to 85 F at 78 F
# wet bulb (35.0 -> 29.4 C at 25.6 C), 2 axial fans of 3 HP (2.2 kW) moving 23,600 cfm each, PVC
# film fill (EVAPCO AT catalog 331D, https://www.evapco.eu/sites/evapco.eu/files/inline-files/at_catalog_331d.pdf).
# Air mass flow 47,200 cfm = 22.3 m3/s at 1.16 kg/m3 (derived). Basin and tower pipework 6 m3 and the
# 15 % natural draft with the fans stopped are project choices.
EVAPCO_AT_14_99 = TowerRating(flow_m3h=68.1, t_hot=35.0, t_cold=29.44, t_wb=25.56,
                              air_kgs=47200.0 * 0.000471947 * 1.16, fan_kw=4.4,
                              source="EVAPCO AT catalog 331D, AT 14-99")
# Design wet bulb of the reference site: 28.1 C, the 1 % value for Jamshedpur (TMYx 2007-2021,
# climate.onebuilding.org; Tata Motors' truck and engine plants are there, ideas.md 11.4).
SITE_WET_BULB_C = 28.1
# Plate heat exchanger design point (project choice inside the sources): 250 kW, process 60 m3/h from
# 35.0 to 31.4 C against tower water at 30.0 C (68 m3/h): an approach of 1.4 K. Water-water plate
# exchangers reach about 1 K approach (Wessels Wesplate submittal: 1.1 K) with U about 6,000 W/m2 K
# (Alfa Laval heating and cooling hub).
PHE_COOL1 = {"m_process": 60.0 / 3600.0 * 997.0, "t_process_in": 35.0, "t_process_out": 31.4,
             "t_tower_in": 30.0}

# ---------------------------------------------------------------------------------------- cnc-1 --
from .cnc import CncRating   # noqa: E402

# DN Solutions DBC 130 horizontal boring mill: spindle 22 kW continuous / 26 kW 30 min, 3383 N m,
# 2500 rpm, electric supply 70 kVA (DN Solutions DBC 130 II brochure). Base load (hydraulics,
# coolant, chip conveyor, axes, controls) 6 kW and drive efficiency 0.85: project choices.
DBC_130 = CncRating(p_s1_kw=22.0, p_30min_kw=26.0, torque_nm=3383.0, rpm_max=2500.0, supply_kva=70.0,
                    base_kw=6.0, source="DN Solutions DBC 130 II brochure")

# ------------------------------------------------------------------------------------ conveyor-1 --
from .conveyor import ConveyorRating   # noqa: E402

# SEW DRN132S4 5.5 kW IE3, efficiency 89.6 / 90.6 / 90.6 % at 100 / 75 / 50 % load (SEW addendum
# 33089094, 3.3.4); SEW R/F/K 3-stage gear unit up to 96 %; steel load on rollers mu_r 0.01 to 0.02
# (Pulseroller guide) -> 0.015. Rated current 11.1 A derived (P / (sqrt3 V PF eta), PF 0.80 project
# choice). Line speed 0.2 m/s, acceleration 0.1 m/s2, empty drag 300 N: project choices.
ROLLER_CONVEYOR = ConveyorRating(
    p_motor_kw=5.5, eta_motor=((1.0, 0.896), (0.75, 0.906), (0.5, 0.906)), eta_gear=0.96,
    v_mps=0.2, accel_mps2=0.1, mu_r=0.015, roller_drag_n=300.0, i_rated_a=11.1,
    source="SEW DRN132S4 IE3 addendum, SEW gear efficiency, Pulseroller guide")

# ------------------------------------------------------------------- fabrication line stations --
from .fab_stations import PaintRating, PlasmaRating, ScannerRating, ShotBlastRating, WelderRating   # noqa: E402

# Hypertherm XPR300 (400 V): 300 A, 210 V arc at 100 % duty, efficiency 91.75 %, PF 0.98, idle 40.1 W,
# air shield at 7.5 bar (instruction manual 809480; xpr300 product page); 25 mm mild steel at
# 1950 mm/min with O2 plasma; Plymovent DraftMax downdraft table fan 2.2 kW, 2500 m3/h. Low-air
# fault at 6.5 bar (project choice: the plant header cycles 6.9 to 7.5 bar).
XPR300 = PlasmaRating(i_arc=300.0, u_arc=210.0, eff=0.9175, pf=0.98, idle_w=40.1, air_bar_min=6.5,
                      cut_mm_min=1950.0, fan_kw=2.2,
                      source="Hypertherm XPR300 manual 809480 and product page; Plymovent DraftMax")
# Fronius TPS 500i: 360 A at 100 % duty, PF 0.99 (Fronius TPS i flyer); efficiency 90 % (ESAB Aristo
# 500ix datasheet, a comparable inverter); idle power 50 W (project choice: Kemppi X8 open-circuit
# 44 to 53 W, a snippet only). The robot welds at 300 A (project choice).
TPS500I = WelderRating(i_weld=300.0, eff=0.90, pf=0.99, idle_w=50.0,
                       source="Fronius TPS 500i flyer; ESAB Aristo 500ix efficiency")
# Roesler RRB 16/5 roller-conveyor blast machine: 4 Gamma 400G turbines of 11 kW, dust collector
# 7,500 m3/h (Roesler RRB fact sheet). Gamma 400G: 11 to 30 kW, up to 400 kg/min (shotpeener.com
# article). Project choices: idle share 0.35, 150 kg/min per turbine at 11 kW, filter drop 800 Pa
# clean and 1,500 Pa at the pulse-cleaning trigger, fan total pressure 2,500 Pa, fan efficiency 0.70.
RRB_16_5 = ShotBlastRating(turbines=4, turbine_kw=11.0, idle_frac=0.35, abrasive_kg_min=150.0,
                           collector_m3h=7500.0, dp_clean_pa=800.0, dp_clean_trigger_pa=1500.0,
                           fan_dp_pa=2500.0, fan_eta=0.70, source="Roesler RRB fact sheet; Gamma 400G")
# Paint booth for heavy parts: 4 exhaust fans of 15 HP (11.2 kW) at 5 in. water gauge (about 1,245 Pa)
# (BHEL paint booth specification, annexure C). Arrestor pads about 10 Pa clean (Viskon-Aire);
# change at 250 Pa (project choice inside the 128 to 256 Pa of a maker's snippet). Bake oven 80 C
# for the 2K paint cure, gas-fired 200 kW (project choice); oven and part capacity, loss: project.
HEAVY_BOOTH = PaintRating(exhaust_kw=4 * 11.2, dp_clean_pa=10.0, dp_change_pa=250.0, fan_dp_pa=1245.0,
                          oven_kw=200.0, oven_setpoint_c=80.0, oven_c_j_k=8.0e6, oven_ua_w_k=1500.0,
                          source="BHEL paint booth spec annexure C; Viskon-Aire filter")
# Weldment scanner: Keyence LJ-X8000A laser profiler controller (24 VDC, 1.3 A max, about 31 W) on a
# Mean Well NDR-480-24 supply (hold-up 16 ms at full load, works from 90 VAC on 230 VAC = 0.39 pu).
# The industrial PC and lighting make the station 300 W (project choice); reboot 60 s (project).
SCANNER = ScannerRating(p_w=300.0, holdup_s=0.016, v_min_pu=0.39, reboot_s=60.0,
                        source="Keyence LJ-X8000A datasheet; Mean Well NDR-480 spec")

# -------------------------------------------------------------------------------------- press-2 --
from .mech_press import MechPressRating   # noqa: E402

# High-slip press motor (mechanical presses use high-slip motors so that the flywheel gives its
# energy; NEMA MG 1 1.19.1.4 Design D: slip 5 % or more, 12.38.3: locked-rotor torque 275 %+).
# Wannan (WNM) YH2-160L-4: 15 kW (S3 25 %), 380 V 50 Hz, 1380 rpm (slip 8 %), 32.3 A, efficiency
# 82.0 %, PF 0.86, locked-rotor current 5.5 x, locked-rotor torque 2.6 x (WNM YH2 catalog,
# http://www.wnmotor-en.com/UploadFiles/Others/20190429155618_43894.pdf). Not on the sheet, project
# choices: breakdown torque 2.9 x (between its 2.6 locked-rotor torque and the 3.6 of the Baldor
# Design D OF4100T sheet), rotor inertia 0.10 kg m2 and 140 kg (a 160-frame 4-pole motor), part-load
# efficiency equal to the full-load value.
YH2_160L_4 = MotorRating(
    p_kw=15.0, v_ll=380.0, f_hz=50.0, poles=4, n_rpm=1380.0, i_a=32.3,
    eta=0.82, pf=0.86, is_in=5.5, ts_tn=2.6, tmax_tn=2.9, j_kgm2=0.10, eta_75=0.82,
    rise_k=80.0, t_amb_c=40.0, mass_kg=140.0,
    source="WNM YH2-160L-4 high-slip catalog; breakdown, inertia, mass project choices")
# Aida NC1-2000 gap-frame press: 2000 kN rated 6 mm above BDC, 160 mm stroke, 35 to 70 spm, 15 kW,
# clutch air 0.5 MPa, brake stop-time monitoring (Aida NC1 specification sheet 2021). The flywheel is
# sized by the rule that a full-tonnage stroke slows it 15 % (The Fabricator, "Stamping 101": 10 to
# 15 %): 2000 kN x 6 mm = 12 kJ is the 15 % share, so the usable energy to a 20 % slowdown is
# 12 / (0.2775 / 0.36) = 15.6 kJ (derived). Project choices: flywheel 350 rpm, 40 spm, crank and slide
# inertia 4 kg m2, clutch pressure switch at 4.5 bar, brake stop 180 ms and monitor limit 220 ms.
AIDA_NC1_2000 = MechPressRating(
    tonnage_t=2000.0 / 9.81, stroke_mm=160.0, spm_continuous=40.0, flywheel_rpm=350.0,
    flywheel_energy_kj=15.6, j_crank_kgm2=4.0, clutch_min_bar=4.5, brake_stop_ms=180.0,
    brake_limit_ms=220.0, source="Aida NC1 specs 2021; The Fabricator Stamping 101; project choices")

# ---------------------------------------------------------------------- engine assembly, part 1 --
from .assembly import LeakTestRating, NutrunnerRating, PalletConveyorRating, WasherRating   # noqa: E402

# Bosch Rexroth TS 5 pallet transfer: 6 m/min with an SEW DRS71S4 0.37 kW drive per section, pallets to
# 300 kg (TS 5 technical data 3 842 540 380; TS 5 introduction). 8 sections: project choice.
TS5_LINE = PalletConveyorRating(speed_m_min=6.0, section_kw=0.37, sections=8, pallet_kg=300.0,
                                source="Bosch Rexroth TS 5 technical data and introduction")
# Desoutter EAD280-260 angle nutrunner (60 to 250 N m, 6 sigma under 8 % -> 1 sigma 1.33 %) on a CVI3
# controller (mean 1 kVA, peak 5 kW for under 0.5 s, standby 17 W) (Desoutter capability sheet
# 6159992450, CVI3 manual 6159924330). Head bolts to about 180 N m after snug + angle, 6 s per bolt pair,
# the controller resets under 0.7 pu: project choices.
NUTRUNNER = NutrunnerRating(target_nm=180.0, sigma_frac=0.08 / 6.0, bolt_s=6.0, mean_w=1000.0,
                            peak_w=5000.0, standby_w=17.0, v_min_pu=0.7,
                            source="Desoutter EAD capability sheet, CVI3 manual")
# MecWash block washer at an Indian truck maker: wash at 80 C, HP stage 40 bar 125 l/min with an 11 kW
# pump, LP stage 1 to 5 bar 1500 l/min with an 18.5 kW pump, a block every 8 min (Wanner/MecWash case
# study). Tank 2000 l, heater 120 kW (the StingRay 8473 web spec: 2934 l, 120 kW), dryer 15 kW, block
# 450 kg, tank loss 400 W/K: project choices.
MECWASH = WasherRating(t_wash_c=80.0, tank_l=2000.0, heater_kw=120.0, hp_pump_kw=11.0, lp_pump_kw=18.5,
                       dryer_kw=15.0, cycle_s=480.0, part_kg=450.0, loss_w_k=400.0,
                       source="Wanner/MecWash case study; StingRay 8473 web spec")
# Water-jacket pressure-decay test: 12 l volume, 40 s test (InterTech M1075 note); 1.4 bar test pressure
# and a 12 scc/min reject on heads (Cincinnati Test Systems); supply 1 to 2 bar over the test pressure
# (ATEQ F620). Settle 15 s and a 500 Pa reject over 25 s (the 12 scc/min level, derived ~ 400 Pa, with
# margin), part cooling rate: project choices.
LEAK_TEST = LeakTestRating(p_test_bar=1.4, volume_l=12.0, fill_s=10.0, settle_s=15.0, test_s=25.0,
                           reject_pa=500.0, supply_margin_bar=1.0, cool_k_per_s_per_k=0.0015,
                           source="InterTech M1075; Cincinnati Test Systems; ATEQ F620")

# ---------------------------------------------------------------------- engine assembly, tests --
from .assembly import EngineRating   # noqa: E402

# A QSL9-class 8.9 l diesel at its F85 rating: 306 kW at 1760 rpm, 89.1 l/h of fuel, 145 kW to the
# coolant, 36 kW to the room (Cummins CFP9E-F85 spec sheet 0062918). Diesel 0.85 kg/l and LHV
# 42.7 MJ/kg: project choices (typical). Hot test against a Horiba Dynas3 HT 350 (350 kW absorbing,
# regenerative); schedule idle, 25/50/75 % (SciELO diesel plant study), rated (Detroit Diesel hot
# test points), about 14 min; regeneration efficiency 0.90: project choice (not published). Cold test
# drive at 500 rpm for 120 s (SciELO: 80 to 1000 rpm, 2 min), non-regenerative (Siemens whitepaper),
# reject at +-20 % of the reference torque (US 9863847).
QSL9_F85 = EngineRating(displacement_l=8.9, rated_kw=306.0, rated_rpm=1760.0, fuel_l_h_rated=89.1,
                        coolant_kw_rated=145.0, ambient_kw_rated=36.0,
                        source="Cummins CFP9E-F85 spec sheet 0062918")
