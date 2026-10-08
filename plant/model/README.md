# Plant models (sim overhaul)

This folder holds the physics library of the plant sim overhaul (`ideas.md` sections 11.2 and 11.3).
Each module is one shared part model or one machine. Each model has its own verification tests in
`plant/tests/test_part_*.py` and `plant/tests/test_machine_*.py`.

Rules for every model:

- Each number comes from a cited source. A number with no source has the mark "project choice".
- A fault changes a physical parameter only. It never sets an output value.
- `check()` returns the physics violations of the present state. An empty list means sane.
- The tests compare the model with data that the fit or the calibration did not use.

Local copies of the datasheets are in `references/` (gitignored, see `references/INDEX.md`).

Run the tests from the repository root:

```
python -m pytest plant/tests/test_part_motor.py plant/tests/test_part_power.py plant/tests/test_part_heat.py plant/tests/test_part_pump.py -q
```

## Induction motor (`motor.py`)

The model is a double-cage equivalent circuit with leakage saturation, fitted to the datasheet
(method: Pedra et al., IEEE Trans. Energy Conversion 19(2), 2004). Each step solves the circuit in
steady state at the present slip. The shaft speed integrates the torque balance with an implicit
step. A two-mass thermal network (windings, body) gives the winding temperature.

Reference motor: ABB M3BP 180MLB 4, 22 kW, 4-pole, 400 V, 50 Hz, IE3, TEFC (IC411), design N.
Source: [ABB data sheet 3GBP182420-ADC](https://www.baldor.com/api/products/EMM18224-PPN/drawings/EMM18224-PPN_TECH), 2015-12-23.

### Sources

| Item | Source |
|---|---|
| Rated, part-load, starting and no-load data, weight, starting times | ABB data sheet 3GBP182420-ADC |
| Additional load losses: 2.5 % to 0.5 % of input at rated load, square of torque | [ABB TM018 on IEC 60034-2-1](https://library.e.abb.com/public/66734f08bb2145c3b357145589b92f53/TM018%20IEC%2060034-2-1%20Rev%20C%202009_lowres.pdf) |
| Effect of 90 % voltage | IEEE 141-1993, in [US DOE Motor Tip Sheet 9](https://www.energy.gov/sites/prod/files/2014/04/f15/motor_tip_sheet9.pdf), and [EASA](https://easa.com/resources/trade_press/effect-of-voltage-variation-on-induction-motor-characteristics) |
| Leakage split stator / rotor 0.4 / 0.6 (design B or N) | IEEE 112 practice, in [a university lab handout](https://engineering.purdue.edu/~dionysis/EE452/Lab12/Lab12.pdf) |
| Acceptance bands | IEC 60034-1 tolerances, as reproduced in [a manufacturer catalogue](https://www.kirloskaroilengines.com/documents/541738/e8c2ba11-ec76-fda5-41cf-4551a0f57349) |
| Leakage saturation at high current | Boldea and Nasar, *The Induction Machines Design Handbook* |
| Resistance against temperature (copper 234.5, aluminium 225) | IEC 60034-1 |

Project choices: the iron share of the fixed losses (2/3), the friction and windage shape, the
shape of the leakage saturation curve, the split of the rated rise between windings and body
(0.4), the standstill cooling of a TEFC motor (0.35 of rated).

### Verification (2026-10-08)

The fit uses the rated point, the 75 % load efficiency, the no-load current, the starting current,
torque and power factor, the breakdown torque and the two starting times. All other rows are checks
with data that the fit did not use.

| Quantity | Model | Reference | In the fit |
|---|---|---|---|
| Rated current, efficiency, power factor, speed | 41.5 A, 93.2 %, 0.820, 1480 rpm | 41.5 A, 93.3 %, 0.82, 1480 rpm | yes |
| 75 % load: current, efficiency, power factor | 32.6 A, 94.1 %, 0.780 | 32.5 A, 94.1 %, 0.78 | efficiency only |
| 50 % load: current, efficiency, power factor | 24.8 A, 94.4 %, 0.683 | 24.5 A, 94.1 %, 0.69 | no |
| No-load current | 16.6 A | 16.6 A | yes |
| Starting current, torque, power factor | 339 A, 2.82 TN, 0.454 | 340 A, 2.8 TN, 0.48 | yes |
| Breakdown torque | 3.10 TN | 3.1 TN | yes |
| 90 % voltage, rated load: current, slip, efficiency | +10.0 %, +29 %, -0.7 points | +5 to +11 %, +22 to +23 %, -1 to -3 % | no |
| 90 % voltage: starting current | -10.0 % | -10 to -12 % | no |
| V/f at 25 Hz, rated torque: slip speed, current | 20.8 rpm, 42.1 A | equal to 50 Hz (20.0 rpm, 41.5 A) | no |
| Rotor loss of a no-load start | within 5 % of 0.5 J w² | 0.5 J w² (textbook) | no |
| Energy balance over start, load and coast down | within 1 % | conservation | no |
| Winding rise at rated load | 80 K | 80 K (class B) | yes (calibration) |
| Locked rotor to the limit, cold and hot | 28.3 s, 15.2 s (limit 220 °C) | 27 s, 15 s maximum starting time | yes (calibration) |

Faults: bearing friction raises the current and lowers the speed. Blocked cooling raises the winding
temperature and leaves the current almost unchanged. Broken rotor bars raise the slip in proportion
to the cage resistance. A locked shaft draws the starting current and heats the windings to the limit
in the datasheet starting time.

### Known limits

- No harmonic torques: the torque at 300 rpm is 2.99 TN, the datasheet minimum is 2.1 TN. A start
  under heavy load is a little faster in the model than in a real motor.
- The slip rise at 90 % voltage (+29 %) is above the IEEE 141 value for standard motors (+22 %).
- No current sidebands from broken bars, and no supply unbalance yet.
- The fit takes about 15 s per rating. `fits.json` keeps each fit (tracked in git), so a container
  never refits. Raise `FIT_VERSION` in `motor.py` when the circuit or the fit changes.
- One step costs about 15 µs. Large steps (0.1 s) split into substeps: implicit on the stable
  slopes only, at most 2 % of synchronous speed per substep.

## Power network (`power.py`)

A radial network: the 11 kV grid (a source EMF behind its fault impedance), the transformers, the
LV cable feeders, and the buses with their loads. Each tick a backward/forward sweep solves it with
the load powers of the tick. A load sees the new bus voltage in the next tick (one cycle at a 20 ms
tick). A grid dip changes the source EMF (IEC 61000-4-11 class 3 levels in `catalog.DIPS_CLASS3`).
The transformer temperatures follow the IEC 60076-7 exponential model (top oil, hot spot).

| Item | Value | Source |
|---|---|---|
| 11 kV fault level | 500 MVA, X/R 10 | MSEDCL 5/10 MVA transformer specification 2019 (fault level); X/R project choice |
| TR-1, 2000 kVA 11/0.433 kV Dyn11 | uk 6.25 %, P0 1687 W, Pk 12413 W | IS 1180-1:2014 A4 level 2 totals, [BIS summary](https://www.bis.gov.in/wp-content/uploads/2022/04/Transformers-_compressed.pdf) |
| TR-2, 1600 kVA | uk 6.25 %, P0 1527 W, Pk 9773 W | same |
| Temperature rises | top oil 40 K, winding 45 K | IS 1180 |
| Thermal model constants | oil 180 min, winding 4 min, x 0.8, y 1.6, H 1.1 | IEC 60076-7 values as cited by arXiv 1906.01570 |
| LV feeder cable | 3.5C 300 mm² Al XLPE: 0.129 + j0.0703 Ω/km, 452 A | [Polycab LT catalogue](https://cms.polycab.com/media/cxibmmjk/final_lt-cable-catalog_v9_ord-10054.pdf) |

Project choices: the no-load current (1 %, IS 1180 allows 2 %), the average oil rise (0.8 of the
top-oil rise), the 11 kV X/R, the cable lengths, the ambient air of the yard (35 °C).

Verification (2026-10-08, `test_part_power.py`):

| Check | Model | Reference |
|---|---|---|
| Regulation at rated current, PF 0.8 | equal to the IEC 60076-1 formula (4.33 %) | IEC 60076-1 |
| Power balance, grid to loads | exact (1e-6) | conservation |
| LV bus fault current, TR-1 | 40.1 kA | 42.7 kA on an infinite grid (I_N / uk) |
| Grid dip to 70 % | both lines dip together, 0.62 to 0.70 at the MCC | common mode (ideas.md 11.8) |
| Four 22 kW DOL starts on TR-1 | TR-1 MCC dips 1 to 8 %, TR-2 under 0.15 of that | separate transformers isolate the lines |
| Top oil and hot spot at rated load | 40 K and 54.3 K rise, 63 % of the oil rise after tau_o | IEC 60076-7 |

## Heat exchanger and thermal mass (`heat.py`)

Counterflow effectiveness-NTU (Incropera and DeWitt, chapter 11), UA set from one rated duty,
film coefficients with flow^0.8 (Dittus-Boelter), fouling as an extra resistance. Checks: the
textbook limits of the effectiveness, the rated duty, the energy balance, the effects of flow,
fouling and inlet temperature, no heat from cold to hot (`test_part_heat.py`).

## Centrifugal pump (`pump.py`)

Pump curve through the shut-off head and the rated point, efficiency curve with its best point at
the rated flow, operating point on a static-plus-friction system curve. Checks: the affinity laws
(Q ~ n, H ~ n², P ~ n³), the operating point on both curves, wear and throttling effects, zero flow
above the shut-off head (`test_part_pump.py`).

## Cooling loop (`cooling.py`)

The closed process-water loop cool-1: the pump against the loop resistance, the users in parallel
branches (flow shares 1/sqrt(k)), two lumped water masses (supply and return header), and the
chiller between them. Checks (`test_part_cooling.py`): the flow split and the operating point,
a closed valve sends its flow to the other branches, the energy balance, the warming rate when the
users exceed the chiller, and the flow of a worn pump against the analytic solution.

## Protection (`protection.py`)

The IEC 60947-4-1 class 10 overload relay and the ANSI 27 undervoltage relay of the LOG-100 sim,
as shared parts (sources in SCENARIOS.md section 12).

## compressor-1 (`compressor.py`)

A fixed-speed, oil-injected, water-cooled screw compressor with load/unload control, auto stop,
a star-delta starter, a motor overload relay, the receiver and the header. The air end follows
polytropic compression (n = 1.3), the unloaded power falls with the sump blow-down, the oil cooler
is an effectiveness-NTU exchanger behind a thermostatic by-pass valve, and the aftercooler heat goes
to the same water.

Reference machine: Atlas Copco GA 37 W, 7.5 bar variant, on an ABB M3BP 225SMA 4, 37 kW IE3 motor
([ABB data sheet 3GBP222210-ADC](https://www.baldor.com/api/products/EMM22374-PPN/drawings/EMM22374-PPN_TECH)).

| Item | Value | Source |
|---|---|---|
| FAD at 7.0 bar(e) | 116 l/s (ISO 1217 Annex C) | Atlas Copco GA 30-90 leaflet |
| Electrical input at 7.0 bar(e), water-cooled | 40.8 kW | GA 30/37/45 instruction book 2920 1163 04 |
| Unloading, nominal working pressure, safety valve | 7.5, 7.0, 8.5 bar(e) | instruction book |
| Star-delta changeover | 10 s after the start | instruction book |
| Oil by-pass valve | all oil through the cooler at about 55 °C | instruction book |
| Overload relay setting | 42 A in the phase circuit (72.7 A line) | instruction book |
| Unloaded input | 24.9 % of loaded | CAGI sheet GA 37 water-cooled 100 psig |
| Blow-down | about 40 s | US DOE compressed air sourcebook v3, p. 39 |
| Heat split | 76 % oil cooler, 15 % aftercooler, 5 % motor | Kaeser CSD brochure |
| Receiver | 1.5 m³ by V = 0.25 Qc P1 / (fmax dP) | instruction book formula |

Project choices: element shutdown 120 °C, 6 starts per hour (Compressed Air Challenge: size for
6 to 8), auto stop after 10 min unloaded, 0.5 m³ of header pipework, the cooling water flow
(0.9 kg/s, a 10 K rise), the oil flow (1.0 kg/s, for the 70 to 75 °C element outlet), the shaft
power 37.9 kW (calibrated to the 40.8 kW input on the fitted motor).

Verification (2026-10-08, `test_machine_compressor.py`, 11 checks):

| Check | Model | Reference |
|---|---|---|
| Input, FAD, specific power at 7.0 bar(e) | 40.8 kW, 116.3 l/s, 5.85 kW per m³/min | 40.8 kW, 116 l/s, 5.86 |
| Power against pressure | +7.6 % per bar | DOE: +1 % per 2 psi (about +7 % per bar) |
| Unloaded input after blow-down | 25 % of loaded, 90 % of the fall in 15 to 45 s | CAGI 24.9 %, DOE about 40 s |
| Power at 50 % capacity (1.6 gal/cfm storage) | between 74 and 90 % | DOE curves: 87 % (1 gal/cfm), 76 % (3 gal/cfm) |
| Star-delta start | star current about 1/3 of DOL, delta at 10 s, then loaded | star-delta practice, instruction book |
| Header band and start limit over 3 h | inside 6.9 to 7.5 bar(e), at most 6 starts per hour | controller settings |
| Heat to water, element outlet, water rise | 0.96 of shaft power, 70.8 °C, 10.0 K | Kaeser 0.95, book 70 to 75 °C |
| Warmer water | no change while the cooler has capacity, then the element rises; lost water flow trips it | thermostatic by-pass valve, element shutdown |
| Failed-low transducer (F7) | stays loaded, holds the 8.5 bar safety valve, motor at 75.6 A (relay 72.7 A, class 10 does not trip at 1.04 x) | emergent |
| Leak (F6), clogged intake filter | more loaded time; less delivery | DOE leak practice |
| Air mass balance over 1 h | exact (1e-3) | conservation |

## chiller-1 (`chiller.py`)

An air-cooled screw chiller behind a three-way tempering valve. The process loop runs at about
27 °C (above the dew point of a humid plant), but an air-cooled chiller leaves water at 15 °C at
most. The valve mixes chilled water into the process return to hold the process supply. Capacity
and compressor power come from the manufacturer's table (leaving water against ambient air),
held at the table's leaving-water edge (the compressor envelope). Part load, the minimum step,
the anti-recycle timer, a class 10 overload relay and ANSI 27 undervoltage complete it.
Condenser fouling (F9) acts as a hotter day.

Reference machine: Daikin EWAD190AJYNN, R-134a, 2 single-screw compressors, 2 circuits.

| Item | Value | Source |
|---|---|---|
| Capacity and compressor input, LWT 4 to 15 °C, ambient 25 to 44 °C | 184.0 kW / 76.7 kW at 7 °C and 35 °C; 222.5 / 85.4 kW at 14 °C and 35 °C | [Daikin EWAD190-280AJYNN capacity tables](https://www.daikin.es/content/dam/MDM/TechnicalDrawings/AppliedSystems/EN/EWAD190-280AJYNN_capcool_EN.pdf) |
| Minimum capacity, max running current, PF | 12.5 %, 178.2 A, 0.86 | Daikin IOM, table 18 |
| Start-to-start, minimum off | 600 s, 180 s | Daikin control panel manual D-EOMCP00104-14EN, 6.6 |
| Chiller setpoint range | 4 to 14 °C (the model runs at 14 °C) | same manual |
| Part load at constant conditions | P / P_full = PLR (1 + 0.053 (1 - PLR)) | derived: the ESEER / EER ratio of the EWAD-D-SR 190 (2.91 / 2.28) with this table's ambient relief |

Project choices: the tempering arrangement and the 27 °C process supply, the condenser fans
(6 kW), the evaporator design flow (5 K rise), the plant ambient.

Verification (2026-10-08, `test_machine_chiller.py`, 6 checks): the table points; a 44 °C day
takes more than 20 % of the capacity at 14 °C; the valve holds 27 °C on the loop under capacity
with a COP between 2 and 6; above capacity the valve opens fully, the chiller's leaving water
rises and the loop warms past 29 °C in an hour; an undervoltage stop waits out the start-to-start
timer; condenser fouling costs capacity and more than 5 % power at the same load.

Decided 2026-10-08 (the operator: the conventional choice): cool-1 uses a cooling tower with a
plate heat exchanger (below). chiller-1 stays as a part, and the plant can still run on it
(`UtilitiesPlant(cooling="chiller")`).

## Measurement (`sensors.py`)

Each instrument draws its own fixed error inside its accuracy class, adds a small noise, rounds to
its resolution and holds between samples. Faults: drift, stuck. Classes: Pt100 RTD IEC 60751
class B ±(0.3 + 0.005|t|) °C and class A; current transformer class 0.5S (Schneider Electric FAQ
FA243581 on IEC 61869-2); pressure transmitter 0.25 % of span (project choice). Checks in
`test_part_sensors.py`.

## Fuel gas (`gas.py`)

The utility line, a pressure regulator with droop and a minimum differential, the header, and the
burners (orifice flow with the square root of pressure, heat = flow × LHV × efficiency, a
low-gas-pressure switch that locks out until a reset, as EN 746-2 and NFPA 86 require). Checks in
`test_part_gas.py` with test values. The sourced plant values follow with furnace-1.

## Utilities plant (`plant.py`)

The supply, compressor-1, chiller-1 and cool-1 wired together (ideas.md 13.1, phase 1 item 6):
incomer-1 → MV bus → TR-1 (2000 kVA) → psu-a, psu-b; TR-2 (1600 kVA) → psu-c. compressor-1, chiller-1
and the loop pump are on psu-b. press-1 is on psu-a, its oil cooler a loop branch that its
thermostatic valve opens and closes. Each tick: the network solves with the last loads, the machines
step on their bus voltage and their branch water, the loop takes their heat. Placeholders until
the machines exist: one heat branch for the line machines, and the loop pump drive (90 %
efficiency, PF 0.85).

Network rules added for the plant: below 0.7 pu a constant-power load acts as a constant
impedance (the usual load-model rule), and under 0.3 pu of source EMF the buses follow the source
(an interruption is not a load flow).

Verification (2026-10-08, `test_plant_utilities.py`, 6 checks):

| Check | Result |
|---|---|
| 2 h of normal running | MCC 430 to 433 V, loop supply 27.0 °C, compressor 6.9 to 7.5 bar(e), chiller COP about 2.4, about 750 times real time |
| Power balance at the incomer | exact (1e-6) |
| Design flows | pump 60 m³/h at 25 m, compressor branch 0.88 kg/s |
| F7 through the plant | compressor-1 stays loaded at 8.5 bar(e), more power and more heat into cool-1 |
| IEC 61000-4-11 class 3 dips | the chiller rides through all five |
| F11, a 70 % sag for 4 s | the chiller trips on undervoltage, waits 180 s off and 600 s start to start, the loop warms more than 1 K, then recovers to 27 °C |
| press-1 on psu-a | its bend current dips psu-a; psu-c on TR-2 moves less than 0.2 of that; the oil stays at 35 to 60 °C |

## press-1 (`press_brake.py`)

A CNC hydraulic press brake: the 37 kW motor at constant speed, a fixed-displacement pump, the
two cylinders on the ram, a relief valve, the oil tank, and a water-cooled oil cooler on cool-1
behind a thermostatic valve. The PLC runs the cycle: approach (gravity and prefill), bend, hold,
decompress, return, part handling. The bend pressure comes from the air-bending force of the
plate. Pump leakage follows the oil viscosity (ISO VG 46, ASTM D341), so hot oil bends slower and
makes more heat. Every hydraulic loss goes into the oil.

Reference machine: LVD PPEB 320/40 ([brochure 2021](https://www.lvdgroup.com/sites/default/files/uploads/downloads/PPEB_2021_EN.pdf)).

| Item | Value | Source |
|---|---|---|
| Force, length, stroke | 3200 kN, 4000 mm, 300 mm | LVD PPEB brochure |
| Approach, working, return speed | 120, 14, 130 mm/s | LVD PPEB brochure |
| Main motor, tank, maximum pressure | 37 kW, 400 l, 285 bar | LVD PPEB brochure |
| Cylinder area | 1123 cm² | derived: force / pressure |
| Annulus area | 0.105 of the bore | derived: the same pump flow gives 14 and 130 mm/s |
| Air-bending force | F = 1.42 UTS t² L / V | Durma tonnage rule |
| Plate | S355J2, UTS 510 MPa (470 to 630), 12 mm, 2.5 m, V = 120 mm | British Steel S355J2 sheet; V = 10 t (MetalForming chart) |
| Oil temperature | 35 to 60 °C, not above 70 °C | Durma maintenance notes |
| Oil viscosity | VG 46: 46 cSt at 40 °C, 6.8 cSt at 100 °C | ISO 3448 grade, ASTM D341 relation |

Project choices: ram mass (4 t), pump efficiencies (0.95 volumetric, 0.90 hydraulic-mechanical),
idle circulation (8 bar), guide friction (2 % of force), the water-cooled oil cooler (15 kW, valve
opens at 45 °C; LVD offers an air cooler), 25 s of part handling per bend, the force-penetration
shape.

Verification (2026-10-08, `test_machine_press.py`, 8 checks): the brochure speeds; the bend
pressure equals the air-bending force over the area (196 bar for 2173 kN); the VG 46 viscosity
points; hot oil (65 °C) bends slower than cool oil (40 °C); the oil heat balance; the oil stays
inside 35 to 60 °C with the cooler and trips at 70 °C without water; the idle current near the
motor's no-load current and the bend current under 1.2 x rated; ram guide friction (F1) raises the
bend and return pressures, pump wear (F2) slows the bend, a harder plate (F15) raises the bend
pressure with a healthy machine. One cycle is about 35 s; 30 min of press run in about 1 s.

Not yet: the press's small model (it needs the rhythm discovery of ideas.md 12.3 step 2 first,
because the press has no state signal like the compressor's load state).

## cool-1 cooling tower and plate exchanger (`cooling_tower.py`)

The conventional process cooling: an induced-draft counterflow cooling tower, and a gasketed plate
heat exchanger between the open tower water and the closed process loop. Merkel's equation on the
fill (4-point Chebyshev), KaV/L = C (L/G)^-0.6, C from the maker's rating; saturated air enthalpy
from the Magnus vapour pressure. The fans cycle on a basin thermostat (29 °C, off 1 K under it),
15 % natural draft with the fans stopped. Fill fouling (F9) lowers the characteristic.

| Item | Value | Source |
|---|---|---|
| Tower rating | 68 m³/h, 35.0 → 29.4 °C at 25.6 °C wet bulb, 2 fans 2.2 kW, 47,200 cfm | [EVAPCO AT catalog 331D](https://www.evapco.eu/sites/evapco.eu/files/inline-files/at_catalog_331d.pdf), AT 14-99 |
| Fill exponent | n = -0.6 (range -0.35 to -1.1, mostly -0.55 to -0.65) | SPX Marley TB-R61 |
| Approach against load, evaporation | approach grows with water flow at a fixed range; evaporation 0.00085 × 1.8 × flow × range | BEE guidebook chapter 7 |
| Site design wet bulb | 28.1 °C, 1 % (Jamshedpur) | ISHRAE / TMYx weather files, climate.onebuilding.org |
| Fill fouling | 18 % capability lost on a fouled film pack | Brentwood Industries |
| Plate exchanger | approach about 1 K, U about 6,000 W/m² K for water-water | Wessels Wesplate submittal, Alfa Laval |

Project choices: the basin set point (29 °C), the natural draft (15 %), the basin volume (6 m³),
the plate exchanger design point (250 kW, 1.4 K approach), the tower pump load (3.7 kW).

Verification (2026-10-08): `test_part_cooling_tower.py` (5 checks: the saturated enthalpy, the
rating reproduced to 0.02 K, the approach grows with load and follows the wet bulb and never goes
under it, fouling and a stopped fan, the plate exchanger design duty) and `test_plant_utilities.py`
(the process supply a few kelvin above the 28.1 °C wet bulb with the compressor and the press in
range; a wet bulb of 29.5 °C or fouled fill warms the loop).

## furnace-1 (`furnace.py`)

A gas-fired car-bottom stress-relieving furnace on the plant gas header: four zones along the car,
each with a 375 kW burner (gas.py), a PID on the firing, a chamber node and a share of the load. The
load takes heat by convection and radiation; steel heat capacity from EN 1993-1-2; the lining
loses heat to the room; the flue takes the rest of the gas heat by the DOE available-heat chart.
The program follows AWS D1.1 postweld heat treatment: load under 315 °C, heat at most 220 °C/h per
inch above 315 °C, hold at 595 to 650 °C for 1 h per 25 mm, cool at most 260 °C/h per inch with the
combustion air blower blowing ambient air (burners off), then the car comes out.

| Item | Value | Source |
|---|---|---|
| Program rates, hold, cool | as above | AWS D1.1 as given in the LANL welding standard GWS 1-08; ASME UCS-56 for the hold time |
| Steel heat capacity | 440 J/kg K at 20 °C, 760 at 600 °C | EN 1993-1-2, 3.4.1.2 |
| Available heat, natural gas, 10 % excess air | 63 % at 600 °C, 61 % at 650 °C, 55.6 % at 760 °C | US DOE process heating tip sheet 2 |
| Cooling air | 9.52 m³ air per m³ of methane plus 10 % excess | combustion chemistry |
| Furnace pattern | car-bottom, several zones, high-velocity burners, ceramic fibre, skin under 80 °C | Nutec Bickley, Pyradia, BHEL tender 2621200030 |

Project choices (no maker table found): 25 t of steel plus 6 t of car per batch, 4 zones of 375 kW,
150 m² of load surface, the lining loss (0.65 W/m² K), the chamber heat capacity, the convection
coefficient and the emissivity, the coupling between zones, the PID gains, the 15 kW blower.

Verification (2026-10-08, `test_machine_furnace.py`, 7 checks): the steel heat capacity and the
available-heat points; the program inside the AWS heating and cooling rates, the hold inside 595 to
650 °C for at least 1 h with the zones within 85 K; the energy balance (gas heat = flue + lining +
load + chamber, to 1e-6); a flame failure in one zone makes it lag and lengthens the cycle; a low gas
supply (F10) locks the burners out; a leaking door seal costs gas; a thermocouple that reads 20 K
high leaves a cold spot (smaller than 20 K: the neighbouring zones heat it). One cycle (about 20 h,
about 500 m³ of gas for 25 t) simulates in under a second.

## cnc-1 (`cnc.py`)

A horizontal boring mill machining the four pin bores of a boom (rough, semi-finish and finish
passes). Cutting power by the Sandvik boring formula Pc = vc ap fn kc / 60,000 with the Kienzle
kc = kc1 hm^-mc (S355 in Sandvik P1.1/P1.2: kc1 1,500 to 1,770, mc 0.25; the model uses 1,700). Flank
wear grows with cutting time and raises the power by 0.11 % per um (Kovalcik et al., MM Science
Journal 2020: +27 % at 244 um); the tool changes at the end of the pass that crosses VB 0.3 mm
(ISO 3685). The spindle drive is 22 kW continuous, 26 kW for 30 min, 3,383 N m, 2,500 rpm, 70 kVA
supply (DN Solutions DBC 130 II brochure). A VFD undervoltage trip (under 75 % for 50 ms, project
choice) gives the F11 path.

Project choices: the pass parameters (vc, ap, fn, bore sizes), the wear rate (about 60 min of
cutting per insert), the 6 kW base load, the 0.85 drive efficiency, the load and tool-change times.
Verification (`test_machine_cnc_conveyor.py`): the formula, the +27 % at 244 um, tool changes with at
most one pass of wear over the limit, a broken tool falls to the air-cut level, a 70 % sag trips the
drive.

## conveyor-1 (`conveyor.py`)

A powered roller conveyor moving weldments between stations: F = drag + m g mu_r (+ m a on start),
mu_r 0.015 for steel on steel rollers (Pulseroller guide: 0.01 to 0.02), through an SEW 3-stage gear
unit (96 %) and an SEW DRN132S4 5.5 kW IE3 motor (efficiency 89.6 / 90.6 / 90.6 % at 100 / 75 / 50 %
load, SEW addendum 33089094). A jam (F3) holds the load: the motor draws locked-rotor current and its
class 10 relay trips in seconds; a seized roller raises the drag. Project choices: speed 0.2 m/s,
acceleration 0.1 m/s², 12 m length, 300 N empty drag, PF 0.80, the efficiency under 50 % load.
Verification (`test_machine_cnc_conveyor.py`): the power for a 5 t weldment from the formula, the jam
trip in 2 to 15 s, a seized roller more than 1.5 x the power.

## press-2 (`mech_press.py`)

A mechanical punching press with a flywheel and a pneumatic clutch-brake on a high-slip motor: each
stroke takes the clutch engagement energy (twice the kinetic energy of the crank and slide) and the
punching work F t c out of the flywheel over the last 30 degrees of crank; the motor's slip rises and
it brings the flywheel back before the next stroke. Brake stopping-time monitor (OSHA 1910.217
(b)(14)); clutch air from the plant air header through a 4.5 bar pressure switch (F6 path).

| Item | Value | Source |
|---|---|---|
| Press | 2000 kN at 6 mm above BDC, 160 mm stroke, 35 to 70 spm, 15 kW, clutch air 0.5 MPa | Aida NC1 specification sheet 2021 |
| Flywheel slowdown per stroke | 10 to 15 % | The Fabricator, "Stamping 101" |
| Flywheel energy | 15.6 kJ to a 20 % slowdown | derived: a full-tonnage stroke (2000 kN x 6 mm) is the 15 % share |
| Motor | Wannan YH2-160L-4, 15 kW, 380 V 50 Hz, slip 8 %, 32.3 A, 82 %, PF 0.86, 5.5 x LRC, 2.6 x LRT | WNM YH2 catalog |
| High-slip convention | Design D: slip 5 % or more, locked-rotor torque 275 % or more | NEMA MG 1 1.19.1.4, 12.38.3 |

Project choices: breakdown torque 2.9 x, rotor inertia 0.10 kg m², 140 kg, flywheel 350 rpm, 40 spm,
crank and slide inertia 4 kg m², the 30-degree working angle, the blank (400 mm cut in 10 mm S355,
shear 400 MPa, c 0.5), brake stop 180 ms and monitor limit 220 ms. A design N motor holds its speed
and draws about 4 x rated current at each stroke; the high-slip motor stays near 2 x.

Verification (2026-10-08, `test_machine_press2.py`, 5 checks): a normal stroke slows the flywheel
10 to 15 % (13.4 %) and it recovers before the next stroke, peak current under 3 x rated; low clutch
air blocks the strokes; a worn brake trips the monitor; bearing friction raises the idle current and
belt slip lowers the flywheel speed; a thicker blank deepens the slowdown with the same idle current.
In the plant: press-2 runs on psu-a with its clutch air from the plant header, and a large air leak
(F6) stops both press-2 and the plasma.

## Engine assembly line, part 1 (`assembly.py`)

| Station | Model | Sources | Project choices |
|---|---|---|---|
| pallet-1 | roller pallet transfer, one engine per takt; a jam holds the transfers, the drives slip on their roller clutches | Bosch Rexroth TS 5 (6 m/min, SEW DRS71S4 0.37 kW per section, 300 kg pallets) | 8 sections, 3 m per station, takt 290 s (about 300 engines/day on 24 h) |
| tight-1 | snug + angle tightening, final torque scatter from the tool capability, controller mean/peak power, reset under 0.7 pu | Desoutter EAD280-260 (6 sigma under 8 %), CVI3 (1 kVA mean, 5 kW peak, 17 W standby) | 180 N m head bolts, 6 s per bolt pair, the 0.7 pu reset |
| washer-1 | 80 °C tank heat balance with heater, parts, losses; HP and LP spray pumps; dryer; a heater control fault holds the tank hotter (F13) | MecWash block line at an Indian truck maker (80 °C, 11 kW HP and 18.5 kW LP pumps, 8 min per block); StingRay 8473 (120 kW heater) | 2000 l tank, 15 kW dryer, 450 kg block, 400 W/K loss, the part leaves 15 K under the tank |
| leak-1 | pressure decay = leak (P_atm Q t / V) + the part cooling during the test (ideal gas, dP/P = dT/T, 0.34 % per K); low air means no fill | InterTech (12 l water jacket, 40 s); Cincinnati Test Systems (1.4 bar, 12 scc/min reject); ATEQ F620 (supply 1 to 2 bar over test) | 15 s settle, 25 s test, 500 Pa reject, part cooling rate |

Verification (2026-10-08, `test_machine_assembly.py`, 4 checks): the leak decay from the formula; a
tight part 30 K warm fails (the F13 false reject) and a tight part at room temperature passes; low
air gives no fill; the washer tank at 80 °C and a 10 K control offset leaves the block more than 7 K
warmer; 3 sigma torque scatter under 4 % and a worn socket more than 2.5 x wider; a dip resets the
nutrunner controller; one engine per takt and a jam holds the line.

### Test stands (`assembly.py`: ColdTest, HotTest)

| Stand | Model | Sources | Project choices |
|---|---|---|---|
| cold-1 | the unfired engine turned at 500 rpm for 120 s; torque = motored FMEP x displacement / 4 pi; a tight bearing (F16, a product fault) raises it; reject outside +-20 % of the reference; non-regenerative drive (brake resistor) | Heywood motored-friction correlation; SciELO diesel plant study (80 to 1,000 rpm, 2 min); Siemens test stand whitepaper; US 9863847 (+-20 %) | the test speed, the drive efficiency 0.9 |
| hot-1 | the fired engine through idle, 25/50/75 %, rated, idle against a regenerative AC dyno; heat to coolant and exhaust; the dyno feeds the brake power back (a negative load on psu-c); a dyno trip (F12) stops it at once | Cummins CFP9E-F85 (QSL9: 306 kW, 89.1 l/h, 145 kW coolant, 36 kW room); Horiba Dynas3 HT 350; Detroit Diesel hot-test points; Gao 2019 (more coolant share at low load) | part-load efficiency and coolant-share shapes, regeneration 0.90, 5 kW cell services |

Verification (`test_machine_assembly.py`, 2 more checks): the cold-test reference torque from the
correlation, healthy engines pass and a 35 % tight bearing fails; at rated load the hot test gives
306 kW brake and the spec sheet's 145 kW to coolant, feeds back more than 250 kW, a larger coolant
share at 25 % load, and a dyno trip turns the cell into a small positive load.

In the plant (`test_assembly_line_on_tr2_with_f12_and_f13`): the whole assembly line runs on TR-2
(psu-c), the hot test's engine coolant and cnc-1's spindle heat go onto cool-1 (the placeholder heat for
the machines still without a branch fell from 120 to 40 kW), each washed block is leak-tested after its
transfer (two stations with blow-off air halve its excess temperature: a project choice; without it
every block failed, as real lines know, which is why they cool the block first). A dyno trip (F12)
raises TR-2's load by more than 250 kW at once; a washer that runs 15 K hot (F13) turns tight blocks
into leak-test rejects. Checks that study the utilities or the fabrication line alone build the plant
with `with_assembly=False`, so psu-c and the loop carry no assembly load.
