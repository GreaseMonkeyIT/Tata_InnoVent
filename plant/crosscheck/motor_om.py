#!/usr/bin/env python3
"""Cross-check of the induction motor of plant/model/motor.py with OpenModelica (task 23, 2026-10-28).

The reference is the induction machine of the Modelica Standard Library 4.0.0
(Modelica.Electrical.Machines.BasicMachines.InductionMachines.IM_SquirrelCage): a dynamic space-phasor
model with the electrical transients, solved by OpenModelica. motor.py solves the same equivalent
circuit in steady state at each step (quasi-static) and integrates the shaft speed. In steady state the
two must agree; in a start the dynamic model adds the electrical transients.

The library machine has one cage. motor.py has two in parallel (the double-cage circuit of Pedra et
al. 2004, for the deep-bar effect). Two library cage components on the same rotor space phasor are that
circuit (their currents add at the same rotor voltage), so the model here extends IM_SquirrelCage with
a second cage (`IM_DoubleCage`). Both models then have the same circuit, with two known differences:
the library has no leakage saturation (motor.py's `sat`, which acts above 1.5 x rated current, is set to
0 for the check), and it puts the core-loss conductance before the stator leakage, motor.py after it.

The motor: the ABB M3BP 180MLB 4 (22 kW), at its datasheet reference temperature. The circuit is
written per phase of a delta connection (3 x the star equivalent), as the library's DOL example.

Checks (each with and without the losses: iron, friction and windage, additional load losses):
  1. steady state at 15 slips from standstill to a quarter of the rated slip: line current, P, Q and the
     air-gap torque (without losses) or the shaft torque (with them);
  2. a direct-on-line start against the rated torque of a quadratic load (a pump or a fan) with the
     rotor and the same inertia again on the shaft: the time to 90 % of rated speed, the final speed.

    python plant/crosscheck/motor_om.py                       # writes the model, runs it if omc is on PATH
    python plant/crosscheck/motor_om.py --omc /usr/bin/omc --out /tmp/om

OpenModelica comes from build.openmodelica.org (its apt repository) and installs the Modelica Standard
Library from libraries.openmodelica.org. Without omc the script writes the model, the .mos script and
motor.py's reference values, says which hosts to allow, and exits with code 2.
"""
import argparse
import csv
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from model import catalog as C                                   # noqa: E402
from model.motor import InductionMotor, fit, mech_losses, solve  # noqa: E402

RATING = "ABB_M3BP_180MLB4_22KW"
HOSTS = ("build.openmodelica.org (the OpenModelica apt repository)",
         "libraries.openmodelica.org (the package index that installs the Modelica Standard Library 4.0.0)")
TOL_IDEAL = 0.005           # steady state, no losses: the same circuit, numerics only (0.5 %)
TOL_LOSS = 0.02             # with losses: the core conductance sits elsewhere, the stray load follows I^2
TOL_START = 0.10            # the start: the dynamic model adds the electrical transients
SETTLE_S, AVG_S = 2.5, 0.2  # each plateau: run 2.5 s at constant speed, average the last 10 cycles


def slips(r):
    s_n = r.s_n
    return [1.0, 0.6, 0.4, 0.25, 0.15, 0.1, 0.07, 0.05, 0.035, 0.025, 1.25 * s_n, s_n, 0.75 * s_n,
            0.5 * s_n, 0.25 * s_n]


def circuit(r, losses):
    """motor.py's fitted circuit for the check: no leakage saturation; without losses also no iron,
    friction and additional load losses."""
    c, _ = fit(r)
    c = replace(c, sat=0.0)
    if not losses:
        c = replace(c, rfe=1e15, p_fw_n=0.0, p_ll_n=0.0)
    return c


def msl_params(r, c):
    """The library parameters of a delta-connected machine with motor.py's circuit (3 x the star
    equivalent per phase; phase voltage = line voltage)."""
    w = 2.0 * math.pi * r.f_hz
    k = 3.0
    w_n = r.n_rpm * math.pi / 30.0
    lossy = c.rfe < 1e14
    return {"p": r.poles // 2, "fNominal": r.f_hz, "VsNominal": r.v_ll,
            "Rs": k * c.r1, "Lssigma": k * c.x1 / w, "Lm": k * c.xm / w,
            "Rr": k * c.r2a, "Lrsigma": k * c.x2a / w, "Rr2": k * c.r2b, "Lrsigma2": k * c.x2b / w,
            "Jr": r.j_kgm2,
            # the core conductance G = PRef / (m VRef^2) per phase = 1 / (3 Rfe)
            "PcoreRef": (r.v_ll ** 2 / c.rfe) if lossy else 0.0, "VcoreRef": r.v_ll,
            "PfricRef": c.p_fw_n, "wFricRef": w_n,
            "PstrayRef": c.p_ll_n, "IstrayRef": r.i_a / math.sqrt(3.0), "wStrayRef": w_n,
            "TLoad": r.t_n, "wLoad": w_n, "JLoad": r.j_kgm2}


def modelica(r, ideal, lossy):
    """The package VisrMotorCheck: the double-cage machine, a rig per loss case, the plateau and the
    start models."""
    def rig(name, q):
        return f"""
  model {name} "Supply, terminal box, sensors and the machine with the parameters of motor.py"
    extends Modelica.Icons.Example;
    parameter SI.Voltage VsNominal = {q['VsNominal']!r} "RMS voltage per phase (delta: the line voltage)";
    parameter SI.Frequency fNominal = {q['fNominal']!r};
    IM_DoubleCage imc(p={q['p']}, fsNominal=fNominal, Rs={q['Rs']!r}, TsRef=293.15, alpha20s=0,
      Lszero={q['Lssigma']!r}, Lssigma={q['Lssigma']!r}, Jr={q['Jr']!r}, Js={q['Jr']!r},
      frictionParameters(PRef={q['PfricRef']!r}, wRef={q['wFricRef']!r}, power_w=2),
      statorCoreParameters(PRef={q['PcoreRef']!r}, VRef={q['VcoreRef']!r}, wRef=2*pi*fNominal),
      strayLoadParameters(PRef={q['PstrayRef']!r}, IRef={q['IstrayRef']!r}, wRef={q['wStrayRef']!r}, power_w=1),
      Lm={q['Lm']!r}, Lrsigma={q['Lrsigma']!r}, Rr={q['Rr']!r}, TrRef=293.15, alpha20r=0,
      Lrsigma2={q['Lrsigma2']!r}, Rr2={q['Rr2']!r}, TsOperational=293.15, TrOperational=293.15);
    Modelica.Electrical.Polyphase.Sources.SineVoltage sineVoltage(final m=m, f=fill(fNominal, m),
      V=fill(sqrt(2/3)*VsNominal, m));
    Modelica.Electrical.Polyphase.Basic.Star star(final m=m);
    Modelica.Electrical.Analog.Basic.Ground ground;
    Modelica.Electrical.Machines.Sensors.ElectricalPowerSensor powerSensor;
    Modelica.Electrical.Machines.Sensors.CurrentQuasiRMSSensor currentQuasiRMSSensor;
    Modelica.Electrical.Machines.Utilities.TerminalBox terminalBox(terminalConnection="D");
  initial equation
    imc.is = zeros(3);
    imc.ir = zeros(2);
    imc.cage2.i = zeros(2);
  equation
    connect(star.pin_n, ground.p);
    connect(sineVoltage.plug_n, star.plug_p);
    connect(sineVoltage.plug_p, powerSensor.plug_p);
    connect(powerSensor.plug_nv, star.plug_p);
    connect(powerSensor.plug_ni, currentQuasiRMSSensor.plug_p);
    connect(currentQuasiRMSSensor.plug_n, terminalBox.plugSupply);
    connect(terminalBox.plug_sn, imc.plug_sn);
    connect(terminalBox.plug_sp, imc.plug_sp);
  end {name};
"""
    q = ideal
    return f"""within ;
package VisrMotorCheck "Cross-check of plant/model/motor.py ({r.source.split(',')[0] if r.source else RATING}) with the Modelica Standard Library 4.0.0. Generated by plant/crosscheck/motor_om.py: edit that script, not this file."
  import Modelica.Units.SI;
  import Modelica.Constants.pi;
  constant Integer m = 3 "Number of phases";

  model IM_DoubleCage "IM_SquirrelCage with a second cage in parallel on the rotor space phasor: the double-cage circuit of motor.py"
    extends Modelica.Electrical.Machines.BasicMachines.InductionMachines.IM_SquirrelCage;
    parameter SI.Inductance Lrsigma2 "Second cage stray inductance per phase";
    parameter SI.Resistance Rr2 "Second cage resistance per phase at TrRef";
    Modelica.Electrical.Machines.BasicMachines.Components.SquirrelCage cage2(final Lrsigma=Lrsigma2,
      final Rr=Rr2, final useHeatPort=true, final T_ref=TrRef,
      final alpha=Modelica.Electrical.Machines.Thermal.convertAlpha(alpha20r, TrRef));
  equation
    connect(airGap.spacePhasor_r, cage2.spacePhasor_r);
    connect(cage2.heatPort, internalThermalPort.heatPortRotorWinding);
  end IM_DoubleCage;
{rig("RigIdeal", ideal)}{rig("RigLoss", lossy)}
  model PlateauIdeal "The ideal machine at a constant speed (slip)"
    extends RigIdeal;
    parameter Real slip = 0.02;
    Modelica.Mechanics.Rotational.Sources.ConstantSpeed speed(w_fixed=(1 - slip)*2*pi*fNominal/imc.p);
  equation
    connect(imc.flange, speed.flange);
  end PlateauIdeal;

  model PlateauLoss "The machine with its losses at a constant speed (slip)"
    extends RigLoss;
    parameter Real slip = 0.02;
    Modelica.Mechanics.Rotational.Sources.ConstantSpeed speed(w_fixed=(1 - slip)*2*pi*fNominal/imc.p);
  equation
    connect(imc.flange, speed.flange);
  end PlateauLoss;

  model StartLoss "A direct-on-line start against the rated torque of a quadratic load"
    extends RigLoss(imc(phiMechanical(fixed=true), wMechanical(fixed=true, start=0)));
    Modelica.Mechanics.Rotational.Components.Inertia loadInertia(J={lossy['JLoad']!r});
    Modelica.Mechanics.Rotational.Sources.QuadraticSpeedDependentTorque quadraticLoadTorque(
      w_nominal={lossy['wLoad']!r}, TorqueDirection=false, tau_nominal=-{lossy['TLoad']!r});
  equation
    connect(imc.flange, loadInertia.flange_a);
    connect(loadInertia.flange_b, quadraticLoadTorque.flange);
  end StartLoss;
end VisrMotorCheck;
"""


VARS = "time|imc.tauElectrical|imc.tauShaft|imc.wMechanical|currentQuasiRMSSensor.I|powerSensor.P|powerSensor.Q"


def mos(r, out):
    lines = ['loadModel(Modelica, {"4.0.0"}); getErrorString();',
             f'loadFile("{os.path.join(out, "VisrMotorCheck.mo")}"); getErrorString();', f'cd("{out}");']
    for case in ("Ideal", "Loss"):
        for k, s in enumerate(slips(r)):
            lines.append(f'simulate(VisrMotorCheck.Plateau{case}, stopTime={SETTLE_S}, numberOfIntervals=5000, '
                         f'tolerance=1e-8, outputFormat="csv", fileNamePrefix="plateau_{case}_{k}", '
                         f'variableFilter="{VARS}", simflags="-override=slip={s!r}"); getErrorString();')
    lines.append(f'simulate(VisrMotorCheck.StartLoss, stopTime=3.0, numberOfIntervals=30000, tolerance=1e-8, '
                 f'outputFormat="csv", fileNamePrefix="start_Loss", variableFilter="{VARS}"); getErrorString();')
    return "\n".join(lines) + "\n"


def reference(r):
    """motor.py's values: per case, the steady state at each slip, and the start."""
    out = {}
    w_sync = r.w_sync
    for case, losses in (("Ideal", False), ("Loss", True)):
        c = circuit(r, losses)
        rows = []
        for s in slips(r):
            op = solve(c, r, s, r.v_ll, r.f_hz, r.t_ref_c)
            w = (1.0 - s) * w_sync
            t_shaft = op.t_em - mech_losses(c, r, w)[0]
            t_air = op.p_ag / w_sync                   # the air-gap torque (motor.py takes the LL out of it)
            rows.append({"slip": s, "I": op.amps, "P": op.p_in, "Q": op.q_in, "tau_air": t_air, "tau_shaft": t_shaft})
        out[case] = rows
    out["start"] = start_reference(r)
    return out


def start_reference(r, dt=0.002, t_end=3.0):
    """motor.py's start: the circuit with its losses and no saturation, the rotor and the same inertia
    again on the shaft, a quadratic load torque reaching the rated torque at the rated speed."""
    m = InductionMotor("check", r, j_load=r.j_kgm2, t_cool_c=r.t_ref_c)
    m.c = circuit(r, True)
    m.t_wind = m.t_body = r.t_ref_c
    w_n = r.n_rpm * math.pi / 30.0
    traj, t = [], 0.0
    while t < t_end - 1e-12:
        m.step(dt, r.v_ll, r.f_hz, lambda w: r.t_n * (w / w_n) ** 2)
        m.t_wind = m.t_body = r.t_ref_c          # hold the temperature the library runs at
        t += dt
        traj.append((t, m.w, m.amps))
    return traj


def t90(traj, w_n):
    return next((t for t, w, *_ in traj if w >= 0.9 * w_n), None)


def read_csv(path):
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        rows = [{k.strip('"'): float(v) for k, v in row.items()} for row in rd]
    return rows


def plateau(path):
    rows = read_csv(path)
    t_end = rows[-1]["time"]
    tail = [x for x in rows if x["time"] >= t_end - AVG_S]
    avg = lambda k: sum(x[k] for x in tail) / len(tail)       # noqa: E731
    return {"I": avg("currentQuasiRMSSensor.I"), "P": avg("powerSensor.P"), "Q": avg("powerSensor.Q"),
            "tau_air": avg("imc.tauElectrical"), "tau_shaft": avg("imc.tauShaft")}


def compare(r, ref, out):
    """The table of differences and the verdict."""
    ok, lines = True, []
    for case, tol in (("Ideal", TOL_IDEAL), ("Loss", TOL_LOSS)):
        tau = "tau_air" if case == "Ideal" else "tau_shaft"
        for k, row in enumerate(ref[case]):
            om = plateau(os.path.join(out, f"plateau_{case}_{k}_res.csv"))
            errs = {q: (om[q] - row[q]) / max(abs(row[q]), 1e-9) for q in ("I", "P", "Q", tau)}
            bad = [q for q, e in errs.items() if abs(e) > tol]
            ok &= not bad
            lines.append(f"{case:5s} slip {row['slip']:.4f}: " + ", ".join(f"{q} {100 * e:+.2f} %" for q, e in errs.items())
                         + ("" if not bad else f"  OUTSIDE {100 * tol:g} %"))
    om = read_csv(os.path.join(out, "start_Loss_res.csv"))
    w_n = r.n_rpm * math.pi / 30.0
    a, b = t90([(x["time"], x["imc.wMechanical"]) for x in om], w_n), t90(ref["start"], w_n)
    w_end_om, w_end_ref = om[-1]["imc.wMechanical"], ref["start"][-1][1]
    e_t = (a - b) / b if a and b else float("inf")
    e_w = (w_end_om - w_end_ref) / w_end_ref
    ok &= abs(e_t) <= TOL_START and abs(e_w) <= TOL_IDEAL
    lines.append(f"start: time to 90 % speed {a:.3f} s (OpenModelica) against {b:.3f} s (motor.py), {100 * e_t:+.1f} %; "
                 f"final speed {w_end_om * 30 / math.pi:.1f} against {w_end_ref * 30 / math.pi:.1f} rpm")
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rating", default=RATING, help="a MotorRating in plant/model/catalog.py")
    ap.add_argument("--omc", default=shutil.which("omc"), help="the OpenModelica compiler (default: omc on PATH)")
    ap.add_argument("--out", default=None, help="the folder for the model, the script and the results")
    ap.add_argument("--model-only", action="store_true", help="write VisrMotorCheck.mo next to this script and stop")
    a = ap.parse_args(argv)
    r = getattr(C, a.rating)
    text = modelica(r, msl_params(r, circuit(r, False)), msl_params(r, circuit(r, True)))
    if a.model_only:
        with open(os.path.join(HERE, "VisrMotorCheck.mo"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return 0
    out = a.out or tempfile.mkdtemp(prefix="visr-om-")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "VisrMotorCheck.mo"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    with open(os.path.join(out, "run.mos"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(mos(r, out))
    ref = reference(r)
    with open(os.path.join(out, "motor_py_reference.csv"), "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["case", "slip", "I_A", "P_W", "Q_var", "tau_air_Nm", "tau_shaft_Nm"])
        for case in ("Ideal", "Loss"):
            for x in ref[case]:
                wr.writerow([case, x["slip"], x["I"], x["P"], x["Q"], x["tau_air"], x["tau_shaft"]])
    if not a.omc:
        print(f"wrote {out}/VisrMotorCheck.mo, run.mos and motor_py_reference.csv.\n"
              f"OpenModelica (omc) is not installed. To install it, allow these hosts: {'; '.join(HOSTS)}.\n"
              f"Then: omc {out}/run.mos, and run this script again with --omc and --out {out}.")
        return 2
    res = subprocess.run([a.omc, os.path.join(out, "run.mos")], capture_output=True, text=True, cwd=out)
    with open(os.path.join(out, "omc.log"), "w") as fh:
        fh.write(res.stdout + res.stderr)
    ok, lines = compare(r, ref, out)
    print("\n".join(lines))
    print("PASS" if ok else "FAIL", f"(results in {out})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
