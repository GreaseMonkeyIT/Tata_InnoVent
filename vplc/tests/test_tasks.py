"""The task library (FLEET.md 5.3) over simulated time with the cell wiring convention (3.1)."""
import pytest

import tasklib
from conftest import Clock, make_runtime
from st import compile_task

LIBRARY = ["stamping-line", "packaging-cell", "packaging-cell-rush", "utilities", "machining", "furnace"]


def load(name, clock):
    source, manifest = tasklib.read_library_task(name)[:2]
    rt = make_runtime(source, manifest, clock=clock)
    rt.run()
    return rt, manifest


def ready_all(rt, n):
    rt.field_write("IX", 0, [True] * n)


def run_for(rt, clock, seconds, step_ms=100):
    for _ in range(int(seconds * 1000 / step_ms)):
        clock.advance(step_ms)
        rt.cycle()


@pytest.mark.parametrize("name", LIBRARY)
def test_library_task_compiles_and_manifest_is_valid(name):
    source, manifest = tasklib.read_library_task(name)[:2]
    tasklib.validate_manifest(manifest)
    task = compile_task(source)
    assert task.interval_ms == 100
    cell = tasklib.cell_summary(manifest)
    assert 1 <= len(cell["machines"]) <= 8 and cell["rail"]


def test_stamping_line_speed_follows_derate_ready_and_enable():
    clock = Clock()
    rt, _ = load("stamping-line", clock)
    ready_all(rt, 2)
    run_for(rt, clock, 0.3)
    assert rt.image.qx[0] and rt.image.qx[1] and rt.image.qw[0] == 100 and rt.image.qw[1] == 100
    rt.write_mw(10, [55])                           # DERATE press-1
    run_for(rt, clock, 0.2)
    assert rt.image.qw[0] == 55 and rt.image.qw[1] == 100
    rt.field_write("IX", 1, [False])                # press-2 tripped
    run_for(rt, clock, 0.2)
    assert rt.image.qx[1] is False and rt.image.qw[1] == 0
    rt.write_mw(8, [0])                             # CELL_ENABLE off
    run_for(rt, clock, 0.2)
    assert rt.image.qx[0] is False and rt.image.qw[0] == 0


@pytest.mark.parametrize("name,on_s,off_s", [("packaging-cell", 20, 10), ("packaging-cell-rush", 10, 5)])
def test_packaging_wrapper_cycles_and_counts_packs(name, on_s, off_s):
    clock = Clock()
    rt, _ = load(name, clock)
    ready_all(rt, 3)
    run_for(rt, clock, 1)
    assert rt.image.qx[0] and rt.image.qx[1]        # conveyor and wrapper run
    run_for(rt, clock, on_s)
    assert rt.image.qx[0] and not rt.image.qx[1]    # wrapper pause
    run_for(rt, clock, off_s + 1)
    assert rt.image.qx[1]                           # wrapping again
    assert rt.image.mw[20] >= 1                     # pack counter


# ---------------------------------------------------- LOG-100: the new controllers --
def test_utilities_compressor_band_min_stop_and_chiller_limit():
    clock = Clock()
    rt, manifest = load("utilities", clock)
    assert tasklib.cell_summary(manifest) == {"name": "utilities", "rail": "psu-b",
                                              "machines": ["compressor-1", "chiller-1"]}
    ready_all(rt, 2)
    rt.field_write("IW", 32, [720])                 # 7.20 bar: inside the band, starts unloaded
    run_for(rt, clock, 0.3)
    assert rt.image.qx[0] and rt.image.qw[0] == 25 and rt.image.mw[23] == 0
    rt.field_write("IW", 32, [689])                 # at or below 6.90 bar: load
    run_for(rt, clock, 0.2)
    assert rt.image.qw[0] == 100 and rt.image.mw[23] == 1
    rt.field_write("IW", 32, [740])                 # inside the band: stays loaded
    run_for(rt, clock, 0.2)
    assert rt.image.qw[0] == 100
    rt.field_write("IW", 32, [750])                 # at 7.50 bar: unload
    run_for(rt, clock, 0.2)
    assert rt.image.qw[0] == 25
    rt.field_write("IW", 32, [550])                 # PS2: the transducer fails low, it stays loaded
    run_for(rt, clock, 5)
    assert rt.image.qw[0] == 100
    rt.write_mw(10, [0])                            # the operator stops compressor-1
    run_for(rt, clock, 0.2)
    assert rt.image.qx[0] is False and rt.image.qw[0] == 0
    rt.write_mw(10, [100])                          # a start inside the minimum stop time waits
    run_for(rt, clock, 60)
    assert rt.image.qx[0] is False
    run_for(rt, clock, 61)
    assert rt.image.qx[0] is True
    assert rt.image.qx[1] and rt.image.qw[1] == 100  # the chiller runs at its full demand limit
    rt.write_mw(11, [70])
    run_for(rt, clock, 0.2)
    assert rt.image.qw[1] == 70
    rt.field_write("IX", 1, [False])                # the chiller's overload relay opened
    run_for(rt, clock, 0.2)
    assert rt.image.qx[1] is False and rt.image.qw[1] == 0


def test_machining_feed_override_and_feed_hold():
    clock = Clock()
    rt, _ = load("machining", clock)
    ready_all(rt, 1)
    run_for(rt, clock, 0.3)
    assert rt.image.qx[0] and rt.image.qw[0] == 100
    rt.write_mw(10, [60])                           # feed override 60 %
    run_for(rt, clock, 0.2)
    assert rt.image.qw[0] == 60
    rt.write_mw(21, [1])                            # feed hold
    run_for(rt, clock, 0.2)
    assert rt.image.qx[0] is False and rt.image.qw[0] == 0


def test_furnace_power_limit_and_heat_enable():
    clock = Clock()
    rt, _ = load("furnace", clock)
    ready_all(rt, 1)
    run_for(rt, clock, 0.3)
    assert rt.image.qx[0] and rt.image.qw[0] == 100
    rt.write_mw(10, [55])                           # power limit 55 %
    run_for(rt, clock, 0.2)
    assert rt.image.qw[0] == 55
    rt.write_mw(21, [0])                            # heat off
    run_for(rt, clock, 0.2)
    assert rt.image.qx[0] is False and rt.image.qw[0] == 0
