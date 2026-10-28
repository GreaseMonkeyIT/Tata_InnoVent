"""Energy and flow tags (task 28, 2026-10-28): what the site's meters count, as new tags only.

The plant gave power per machine and no energy. A real site meters energy: a feeder meter (a power
metering device of IEC 61557-12 with an energy register; energy class 0.5S of IEC 62053-22) on each
outgoing feeder, the incomer's tariff meter, and the flowmeters of the utilities. Every tag here is new;
every existing tag keeps its value (the meters only read the plant).

| Tag | What |
|---|---|
| `<load>.energy_kwh` | the energy register of each feeder meter: the load's power as the load flow took it (below 0.7 pu scaled as a constant impedance, nothing in an interruption), integrated each step; imported energy |
| `hot-1.energy_export_kwh` | the energy fed back (a regenerative load counts both ways) |
| `incomer-1.energy_kwh`, `energy_export_kwh`, `energy_kvarh`, `energy_lead_kvarh`, `energy_kvah` | the tariff meter: active import and export, reactive lagging and leading, apparent (MSEDCL bills HT consumers in kVAh since 1 April 2020 and counts the leading kVArh: the MERC order as summarized by AISSMS IOIT's PQ cell and MSEDCL's kVAh presentation) |
| `feeder-a` to `feeder-d`: `p_kw`, `q_kvar`, `energy_kwh` | each LV cable feeder at its LV main: the power into the cable (the MCC's load and the cable loss) |
| `tr-1`, `tr-2`: `load_loss_kw`, `no_load_loss_kw`, `loss_kw` | the transformer losses that power.py computes: the series (copper) loss of the load flow and the no-load loss at the primary voltage |
| `compressor-1.air_m3min`, `compressor-1.header_m3min` | the free air the compressor delivers, and the air that leaves the receiver for the plant (users, leaks, the safety valve) |
| `cool-1.<branch>_m3h` | the water through each user branch of cool-1 |
| `gas-1.flow_m3h`, `gas-1.volume_m3` | the site gas meter: the flow to the burners and its register |
| `hot-1.fuel_l_h`, `hot-1.fuel_l` | the fuel flow of the engine under test and its register (the engine's fuel at the brake power of the stage, from its rated consumption) |

The registers count from the start of the plant run, warm-up included (project choice: a real register
counts from its installation). The tags are not in the plant documents (wiring.py): the engine does not read
them.
"""
SQRT3 = 3.0 ** 0.5


def load_name(ld):
    """The tag prefix of a bus load's meter."""
    m = getattr(ld, "model", None)
    if m is not None:
        return m.name.removesuffix("-motor")
    for attr in ("obj", "pump", "ch", "ts"):
        o = getattr(ld, attr, None)
        if o is not None:
            return o.name
    return ld.name


class Meters:
    """The energy registers of the site. Each plant step: `begin(plant)` before the load flow keeps the
    bus voltages it scales the loads with, `electric(plant, dt)` after it counts the energy of the load
    flow, `flows(plant, dt)` at the end of the step counts the gas and the fuel of the step."""

    def __init__(self, plant):
        self.loads = []                                     # (bus, load, name)
        for b in plant.net.buses():
            for ld in b.loads:
                self.loads.append((b, ld, load_name(ld)))
        self.kwh = {n: 0.0 for _, _, n in self.loads}
        self.kwh_export = {n: 0.0 for _, _, n in self.loads}
        self.feeders = [br for br in plant.net.branches if br.name.startswith("feeder-")]
        self.feeder_kwh = {br.name: 0.0 for br in self.feeders}
        self.inc = {"kwh": 0.0, "kwh_export": 0.0, "kvarh": 0.0, "kvarh_lead": 0.0, "kvah": 0.0}
        self.gas_m3 = self.fuel_l = 0.0
        self._v_pu = {}

    def begin(self, plant):
        """The bus voltages the load flow of this step scales its loads with (power.py: under 0.7 pu a
        constant-power load acts as a constant impedance)."""
        self._v_pu = {id(b): b.v_pu for b in plant.net.buses()}

    def electric(self, plant, dt):
        h = dt / 3600.0
        if plant.grid.e_pu >= 0.3:                         # an interruption: the loads dropped out
            for b, ld, n in self.loads:
                p = ld.s_va().real * min(1.0, (self._v_pu.get(id(b), 1.0) / 0.7) ** 2) / 1000.0
                if p >= 0.0:
                    self.kwh[n] += p * h
                else:
                    self.kwh_export[n] -= p * h
        s = plant.grid.bus.s_down / 1000.0
        inc = self.inc
        inc["kwh"] += max(s.real, 0.0) * h
        inc["kwh_export"] += max(-s.real, 0.0) * h
        inc["kvarh"] += max(s.imag, 0.0) * h
        inc["kvarh_lead"] += max(-s.imag, 0.0) * h
        inc["kvah"] += abs(s) * h
        for br in self.feeders:
            self.feeder_kwh[br.name] += max((br.child.s_down + br.s_loss).real, 0.0) / 1000.0 * h

    def flows(self, plant, dt):
        h = dt / 3600.0
        self.gas_m3 += plant.gas.flow_m3h * h
        if plant.asm:
            self.fuel_l += plant.hot.fuel_l_h * h

    def tags(self, plant):
        out = {}
        for _, ld, n in self.loads:
            out[f"{n}.energy_kwh"] = self.kwh[n]
        if plant.asm:
            out["hot-1.energy_export_kwh"] = self.kwh_export["hot-1"]
        names = {"kwh": "energy_kwh", "kwh_export": "energy_export_kwh", "kvarh": "energy_kvarh",
                 "kvarh_lead": "energy_lead_kvarh", "kvah": "energy_kvah"}
        out.update({f"incomer-1.{names[k]}": v for k, v in self.inc.items()})
        for br in self.feeders:
            s = br.child.s_down + br.s_loss
            out.update({f"{br.name}.p_kw": s.real / 1000.0, f"{br.name}.q_kvar": s.imag / 1000.0,
                        f"{br.name}.energy_kwh": self.feeder_kwh[br.name]})
        for tr in (plant.tr1, plant.tr2):
            ll, nl = tr.s_loss.real / 1000.0, tr.shunt_s().real / 1000.0
            out.update({f"{tr.name}.load_loss_kw": ll, f"{tr.name}.no_load_loss_kw": nl, f"{tr.name}.loss_kw": ll + nl})
        c = plant.comp
        out["compressor-1.air_m3min"] = c.q_fad * 60.0
        out["compressor-1.header_m3min"] = c.q_out * 60.0
        for b in plant.loop.branches:
            out[f"cool-1.{b.name}_m3h"] = b.q_m3s * 3600.0
        out["gas-1.flow_m3h"] = plant.gas.flow_m3h
        out["gas-1.volume_m3"] = self.gas_m3
        if plant.asm:
            out["hot-1.fuel_l_h"] = plant.hot.fuel_l_h
            out["hot-1.fuel_l"] = self.fuel_l
        return out
