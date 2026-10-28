"""The plant's engineering documents as data (ideas.md 12.3 step 6, the AutomationML-like prior).

A real plant has single-line diagrams, P&IDs and PLC I/O lists. They say, for each machine, which
bus feeds it, which pipes and headers it hangs on, which signals its PLC gives (states, phases,
valve and heater outputs) and which instruments it has. This module writes that for the sim plant.
The engine reads it with one rule for every node and has no list of its own (requirement 2 of
ideas.md 12.1).

Each node: {"feeds": [tags of the media it takes from], "commands": [PLC tags: states, phases,
outputs], "measures": [its own instrument tags], "asset": the machine it belongs to,
"loop_outputs": the outputs of that machine's own PLC control loops}. Counters and per-part records
(holes, parts, last_*) are not time series and stay out (per-part records: ideas.md 17.2, week 2),
except the counters of a tool's use since its change and the force of each hit, which a CNC keeps:
plasma-1.electrode_starts (LOG-116), pierce-1.punch_hits and pierce-1.last_force_kn (LOG-118).

Since 2026-10-18 a machine can have more than one node: "<asset>:loop" for its PLC control loops
(the measured variable in, the controller output out, `loops`) and "compressor-1:air" for the air
header. A node's name before the colon is its asset.
"""
import math


def wiring(p):
    """The documents of plant `p` (a UtilitiesPlant): node -> roles -> tag names."""
    w = {}
    shop = ["site.shop_air_c"] if p.site_weather else []
    outdoor = ["site.dry_bulb_c", "site.wet_bulb_c"] if p.site_weather else []
    air = ["compressor-1.pressure_barg"]
    # the cooling water header: its supply temperature and the loop flowmeter (P&ID, 2026-10-18)
    water = ["cool-1.supply_c", "cool-1.flow_m3h"] if not p.chiller else ["cool-1.supply_c"]

    def add(node, feeds, commands, measures):
        w[node] = {"feeds": list(feeds), "commands": list(commands), "measures": list(measures)}

    add("compressor-1", ["psu-b.voltage_v"] + water + shop, ["compressor-1.state"],
        ["compressor-1.current_a", "compressor-1.power_kw", "compressor-1.element_c", "compressor-1.water_out_c"])
    if not p.chiller:
        add("cool-1", ["psu-b.voltage_v", "compressor-1.water_out_c", "compressor-1.power_kw",
                       "press-1.cooler_valve"] + (["lmp-1.cooler_valve", "hot-1.coolant_kw"] if p.fab or p.asm else [])
            + outdoor, ["cool-1-tower.fan_on"],
            ["cool-1.supply_c", "cool-1.return_c", "cool-1-tower.basin_c", "cool-1.flow_m3h", "cool-1-tower.hot_c"])
    if p.press is not None:
        add("press-1", ["psu-a.voltage_v"] + water, ["press-1.phase", "press-1.cooler_valve"],
            ["press-1.current_a", "press-1.pressure_bar", "press-1.oil_c"])
    if p.fab:
        add("lmp-1", ["psu-a.voltage_v"] + water, ["lmp-1.phase", "lmp-1.cooler_valve"],
            ["lmp-1.current_a", "lmp-1.pressure_bar", "lmp-1.oil_c"])
        # the CNC counts the hits on the punch since its change, and logs the force of each hit
        # (frame_line.py): the counter is a PLC input, the force a measure (2026-10-18)
        add("pierce-1", ["psu-a.voltage_v"], ["pierce-1.state", "pierce-1.punch_hits"],
            ["pierce-1.current_a", "pierce-1.pressure_bar", "pierce-1.last_force_kn"])
        add("press-2", ["psu-a.voltage_v"] + air, ["press-2.state"],
            ["press-2.current_a", "press-2.flywheel_rpm"])
        # the CNC counts the arc starts on the consumables since their change (fab_stations.py)
        add("plasma-1", air, ["plasma-1.state", "plasma-1.electrode_starts"], ["plasma-1.power_kw", "plasma-1.arc_v"])
        for k in (1, 2):
            add(f"weld-{k}", [], [f"weld-{k}.state"], [f"weld-{k}.power_kw", f"weld-{k}.weld_a"])
        add("blast-1", [], ["blast-1.abrasive_kg_min"], ["blast-1.power_kw", "blast-1.filter_dp_pa", "blast-1.air_m3h"])
        add("ced-1", ["gas-1.header_kpa", "chiller-1.power_kw"] + shop, ["ced-1.stage"],
            ["ced-1.bath_c", "ced-1.degrease_c", "ced-1.phosphate_c", "ced-1.oven_c", "ced-1.power_kw",
             "ced-1.rect_a"])
        add("conveyor-1", ["psu-a.voltage_v"], ["conveyor-1.state"], ["conveyor-1.current_a", "conveyor-1.speed_mps"])
    if p.asm:
        add("washer-1", shop, ["washer-1.phase", "washer-1.heater_on"],
            ["washer-1.tank_c", "washer-1.power_kw", "washer-1.part_out_c"])
        # the export limiter is a PLC function: its output, the braking resistor's share, decides how
        # much of the dyno power goes back to the supply (plant.py, the no-export rule)
        add("hot-1", ["hot-1.resistor_kw"], ["hot-1.state", "hot-1.load_pct"], ["hot-1.coolant_kw", "hot-1.power_kw"])
        # pallet-1 and cold-1 have their PLC states since 2026-10-28 (the instrument sheet, ideas.md 17.3):
        # the transfer's line state (move, idle, jam: what sets its drives' power) and the test stand's
        # state (idle, rig, test). pallet-1's 8 section states and its engine count are PLC tags too; the
        # section states do not change the power of the model, so they are not the node's inputs
        add("pallet-1", ["psu-c.voltage_v"], ["pallet-1.state"], ["pallet-1.power_kw"])
        add("cold-1", ["psu-c.voltage_v"], ["cold-1.state"], ["cold-1.power_kw", "cold-1.torque_nm", "cold-1.speed_rpm"])
    # incomer-1: the grid and the transformers; the site load explains the bus voltages. The Class A
    # analyzer at the 11 kV metering (2026-10-22) adds the lowest voltage of its last interval (dips)
    add("incomer-1", ["incomer-1.p_kw", "incomer-1.q_kvar", "tr-1.load_pct", "tr-2.load_pct"], [],
        ["psu-a.voltage_v", "psu-b.voltage_v", "psu-c.voltage_v", "psu-d.voltage_v", "incomer-1.v_min_v"])
    # gas-1: the regulator; the gas flow explains the header pressure (droop)
    if p.fab:
        add("gas-1", ["ced-1.tank_gas_m3h", "ced-1.oven_gas_m3h"], [], ["gas-1.header_kpa"])
    add("tr-1", ["tr-1.load_pct"] + outdoor, [], ["tr-1.oil_c"])
    add("tr-2", ["tr-2.load_pct"] + outdoor, [], ["tr-2.oil_c"])
    # the air header (P&ID): its transmitters are the controller's transducer at the receiver and the
    # pressure gauges at the users. The compressor's delivery (its load state) and the modelled users
    # explain them. It belongs to the air system, so to compressor-1 (2026-10-18).
    hdr = ["compressor-1.pressure_barg"] + (["plasma-1.air_bar", "press-2.clutch_air_bar"] if p.fab else []) \
        + (["leak-1.air_bar"] if p.asm else [])
    add("compressor-1:air", [], ["compressor-1.state"] + (["plasma-1.state"] if p.fab else []), hdr)
    # the PLC control loops (PLC program): one controller node per loop, the measured variable in and
    # the controller output out
    for lp in loops(p):
        add(f"{lp['asset']}:loop", lp["measured"], [], lp["output"])
    own = {}
    for lp in loops(p):
        own.setdefault(lp["asset"], []).extend(lp["output"])
        # the machine's node takes its own loop outputs as inputs (a valve, a burner, a heater acts on
        # what the machine measures); add the ones the lists above do not have
        doc = w[lp["asset"]]
        added = [t for t in lp["output"] if t not in doc["feeds"] + doc["commands"]]
        doc["feeds"] += added
        doc.setdefault("added_inputs", []).extend(added)
    for name, doc in w.items():
        doc["asset"] = name.split(":")[0]
        doc["loop_outputs"] = list(own.get(doc["asset"], []))
    return w


def loops(p):
    """The PLC control loops of plant `p`: [{"asset", "measured": [tags], "output": [tags]}]. Each
    one is in the plant model: the controller acts on the measured tags and sets the output."""
    out = [{"asset": "compressor-1", "measured": ["compressor-1.pressure_barg"], "output": ["compressor-1.state"]}]
    if not p.chiller:
        out.append({"asset": "cool-1", "measured": ["cool-1-tower.basin_c"], "output": ["cool-1-tower.fan_on"]})
    if p.press is not None:
        out.append({"asset": "press-1", "measured": ["press-1.oil_c"], "output": ["press-1.cooler_valve"]})
    if p.fab:
        out.append({"asset": "lmp-1", "measured": ["lmp-1.oil_c"], "output": ["lmp-1.cooler_valve"]})
        out.append({"asset": "ced-1", "measured": ["ced-1.degrease_c", "ced-1.phosphate_c", "ced-1.oven_c"],
                    "output": ["ced-1.tank_gas_m3h", "ced-1.oven_gas_m3h"]})
    if p.asm:
        out.append({"asset": "washer-1", "measured": ["washer-1.tank_c"], "output": ["washer-1.heater_on"]})
    return out


def instrument_ranges(p):
    """The rated value (full range) of each metered quantity, from the ratings of the device it
    meters (the instrument sheet of ideas.md 17.3: a CT and meter are chosen for the feeder they
    measure). instruments.py holds the error of a %-of-reading instrument at its value at 5 % of
    this range below that point (IEC 61869-2 specifies class 0.5S CTs from 1 % to 120 % of rated
    current; the 5 % point is a project choice)."""
    from . import catalog as C
    r = {"compressor-1.current_a": C.ABB_37KW.i_a, "compressor-1.power_kw": C.GA37W_7_5.p_shaft_kw / C.ABB_37KW.eta,
         "incomer-1.p_kw": C.TR_FAB.kva + C.TR_ASM.kva, "incomer-1.q_kvar": C.TR_FAB.kva + C.TR_ASM.kva}
    if p.press is not None:
        r["press-1.current_a"] = C.ABB_37KW.i_a
    if p.fab:
        r.update({
            "lmp-1.current_a": 2 * C.ABB_160KW.i_a,
            "pierce-1.current_a": C.ABB_M3BP_180MLB4_22KW.i_a,
            "press-2.current_a": C.YH2_160L_4.i_a,
            "plasma-1.power_kw": C.XPR300.i_arc * C.XPR300.u_arc / C.XPR300.eff / 1000.0,
            "plasma-1.arc_v": C.XPR300.u_arc,
            "blast-1.power_kw": C.RRB_16_5.turbines * C.RRB_16_5.turbine_kw + 8.0,
            "blast-1.air_m3h": C.RRB_16_5.collector_m3h,
            "conveyor-1.current_a": C.ROLLER_CONVEYOR.i_rated_a,
            "conveyor-1.speed_mps": C.ROLLER_CONVEYOR.v_mps,
            "ced-1.rect_a": 1000.0,                     # rectifier rating (project choice)
            "ced-1.power_kw": C.CED_LINE.volts * 1000.0 / C.CED_LINE.rect_eta / 1000.0
                              + C.CED_LINE.pumps_kw + C.CED_LINE.oven_fans_kw,
            "ced-1.tank_gas_m3h": 2 * C.CED_LINE.tank_burner_kw * 3.6 / C.GAS_SUPPLY.lhv_mj_m3,
            "ced-1.oven_gas_m3h": C.CED_LINE.oven_kw * 3.6 / C.GAS_SUPPLY.lhv_mj_m3,
            "chiller-1.power_kw": 100.0,                # chiller input range (project choice)
        })
        for k in (1, 2):
            r[f"weld-{k}.weld_a"] = C.TPS500I.i_weld
            r[f"weld-{k}.power_kw"] = C.TPS500I.i_weld * (14.0 + 0.05 * C.TPS500I.i_weld) / C.TPS500I.eff / 1000.0
    if p.asm:
        # pallet-1: its 8 section drives; cold-1: twice the reference torque of the test and its power
        # (project choice: the drive's rating is not given)
        t_ref = p.cold.reference_nm()
        r.update({"pallet-1.power_kw": C.TS5_LINE.sections * C.TS5_LINE.section_kw,
                  "cold-1.torque_nm": 2.0 * t_ref,
                  "cold-1.power_kw": 2.0 * t_ref * p.cold.test_rpm * math.pi / 30.0 / p.cold.eta / 1000.0,
                  "cold-1.speed_rpm": p.cold.test_rpm})
        r.update({"washer-1.power_kw": C.MECWASH.heater_kw + C.MECWASH.hp_pump_kw + C.MECWASH.lp_pump_kw + C.MECWASH.dryer_kw,
                  "hot-1.power_kw": C.QSL9_F85.rated_kw, "hot-1.coolant_kw": C.QSL9_F85.coolant_kw_rated,
                  "hot-1.resistor_kw": C.QSL9_F85.rated_kw})
    return r
