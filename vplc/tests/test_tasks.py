"""The task library (FLEET.md 5.3) over simulated time with the cell wiring convention (3.1)."""
import pytest

import tasklib
from conftest import Clock, make_runtime
from st import compile_task

LIBRARY = ["stamping-line", "packaging-cell", "packaging-cell-rush"]


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
