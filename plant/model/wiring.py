"""The plant's engineering documents as data (ideas.md 12.3 step 6, the AutomationML-like prior).

A real plant has single-line diagrams, P&IDs and PLC I/O lists. They say, for each machine, which
bus feeds it, which pipes and headers it hangs on, which signals its PLC gives (states, phases,
valve and heater outputs) and which instruments it has. This module writes that for the sim plant.
The engine reads it with one rule for every node and has no list of its own (requirement 2 of
ideas.md 12.1).

Each node: {"feeds": [tags of the media it takes from], "commands": [PLC tags: states, phases,
outputs], "measures": [its own instrument tags]}. Counters and per-part records (holes, parts,
last_*) are not time series and stay out (per-part records: ideas.md 17.2, week 2).
"""


def wiring(p):
    """The documents of plant `p` (a UtilitiesPlant): node -> roles -> tag names."""
    w = {}
    shop = ["site.shop_air_c"] if p.site_weather else []
    outdoor = ["site.dry_bulb_c", "site.wet_bulb_c"] if p.site_weather else []
    air = ["compressor-1.pressure_barg"]
    water = ["cool-1.supply_c"]

    def add(node, feeds, commands, measures):
        w[node] = {"feeds": list(feeds), "commands": list(commands), "measures": list(measures)}

    add("compressor-1", ["psu-b.voltage_v"] + water + shop, ["compressor-1.state"],
        ["compressor-1.current_a", "compressor-1.power_kw", "compressor-1.element_c", "compressor-1.water_out_c"])
    if not p.chiller:
        add("cool-1", ["psu-b.voltage_v", "compressor-1.water_out_c", "compressor-1.power_kw",
                       "press-1.cooler_valve"] + (["lmp-1.cooler_valve", "hot-1.coolant_kw"] if p.fab or p.asm else [])
            + outdoor, ["cool-1-tower.fan_on"],
            ["cool-1.supply_c", "cool-1.return_c", "cool-1-tower.basin_c"])
    if p.press is not None:
        add("press-1", ["psu-a.voltage_v"] + water, ["press-1.phase", "press-1.cooler_valve"],
            ["press-1.current_a", "press-1.pressure_bar", "press-1.oil_c"])
    if p.fab:
        add("lmp-1", ["psu-a.voltage_v"] + water, ["lmp-1.phase", "lmp-1.cooler_valve"],
            ["lmp-1.current_a", "lmp-1.pressure_bar", "lmp-1.oil_c"])
        add("pierce-1", ["psu-a.voltage_v"], ["pierce-1.state"],
            ["pierce-1.current_a", "pierce-1.pressure_bar"])
        add("press-2", ["psu-a.voltage_v"] + air, ["press-2.state"],
            ["press-2.current_a", "press-2.flywheel_rpm"])
        add("plasma-1", air, ["plasma-1.state"], ["plasma-1.power_kw", "plasma-1.arc_v"])
        for k in (1, 2):
            add(f"weld-{k}", [], [f"weld-{k}.state"], [f"weld-{k}.power_kw", f"weld-{k}.weld_a"])
        add("blast-1", [], ["blast-1.abrasive_kg_min"], ["blast-1.power_kw", "blast-1.filter_dp_pa", "blast-1.air_m3h"])
        add("ced-1", ["gas-1.header_kpa", "chiller-1.power_kw"] + shop, ["ced-1.stage"],
            ["ced-1.bath_c", "ced-1.degrease_c", "ced-1.phosphate_c", "ced-1.oven_c", "ced-1.power_kw",
             "ced-1.rect_a"])
        add("conveyor-1", ["psu-a.voltage_v"], ["conveyor-1.state"], ["conveyor-1.current_a", "conveyor-1.speed_mps"])
    if p.asm:
        add("washer-1", shop, ["washer-1.phase", "washer-1.heater_on"], ["washer-1.tank_c", "washer-1.power_kw"])
        # the export limiter is a PLC function: its output, the braking resistor's share, decides how
        # much of the dyno power goes back to the supply (plant.py, the no-export rule)
        add("hot-1", ["hot-1.resistor_kw"], ["hot-1.state", "hot-1.load_pct"], ["hot-1.coolant_kw", "hot-1.power_kw"])
        # pallet-1 and cold-1 give no PLC state tag yet (week 2, ideas.md 17.2): a model with no input
        # can only predict a mean, so they are not nodes until then
    # incomer-1: the grid and the transformers; the site load explains the bus voltages
    add("incomer-1", ["incomer-1.p_kw", "incomer-1.q_kvar", "tr-1.load_pct", "tr-2.load_pct"], [],
        ["psu-a.voltage_v", "psu-b.voltage_v", "psu-c.voltage_v", "psu-d.voltage_v"])
    # gas-1: the regulator; the gas flow explains the header pressure (droop)
    if p.fab:
        add("gas-1", ["ced-1.tank_gas_m3h", "ced-1.oven_gas_m3h"], [], ["gas-1.header_kpa"])
    add("tr-1", ["tr-1.load_pct"] + outdoor, [], ["tr-1.oil_c"])
    add("tr-2", ["tr-2.load_pct"] + outdoor, [], ["tr-2.oil_c"])
    return w


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
        r.update({"washer-1.power_kw": C.MECWASH.heater_kw + C.MECWASH.hp_pump_kw + C.MECWASH.lp_pump_kw + C.MECWASH.dryer_kw,
                  "hot-1.power_kw": C.QSL9_F85.rated_kw, "hot-1.coolant_kw": C.QSL9_F85.coolant_kw_rated,
                  "hot-1.resistor_kw": C.QSL9_F85.rated_kw})
    return r
