"""The scan engine (FLEET.md 6): system words, overruns, STOP, FAULT, task load, and setpoint writes."""
from conftest import Clock, make_runtime, program
from runtime import FAULT, RUN, STOP, Runtime
from st import compile_task

COUNTER = program("    out AT %QX0.0 : BOOL;\n    sp AT %QW0 : INT;\n    der AT %MW10 : INT := 100;\n    n : INT;",
                  "out := TRUE;\nsp := der;\nn := n + 1;")


def test_system_words_after_each_scan():
    rt = make_runtime(COUNTER)
    rt.run()
    for _ in range(3):
        rt.cycle()
    mw = rt.image.mw
    assert mw[1] == 3                        # scan counter
    assert mw[2] == 1                        # RUN
    assert mw[3] == 0                        # no overruns
    assert mw[4] == rt.task.compiled.crc15   # task checksum
    assert 0 <= mw[0] <= 32767


def test_overrun_counts_a_scan_longer_than_the_interval():
    ticks = iter([0.0, 0.5, 1.0, 1.001])     # perf_counter seconds: 500 ms, then 1 ms
    rt = make_runtime(COUNTER, perf=lambda: next(ticks))
    rt.run()
    rt.cycle()
    rt.cycle()
    assert rt.overruns == 1 and rt.image.mw[3] == 1


def test_stop_clears_outputs_and_run_restores_them():
    rt = make_runtime(COUNTER)
    rt.run()
    rt.cycle()
    assert rt.image.qx[0] is True and rt.image.qw[0] == 100
    rt.stop()
    assert rt.state == STOP and rt.image.qx[0] is False and rt.image.qw[0] == 0
    rt.cycle()
    assert rt.image.qx[0] is False and rt.image.mw[2] == 0
    rt.run()
    rt.cycle()
    assert rt.image.qx[0] is True


def test_runtime_error_faults_and_clears_outputs():
    src = program("    out AT %QX0.0 : BOOL;\n    z : INT;\n    b : INT;", "out := TRUE;\nb := 1 / z;")
    rt = make_runtime(src)
    rt.run()
    rt.cycle()
    assert rt.state == FAULT and "division" in rt.fault
    assert rt.image.qx[0] is False and rt.image.mw[2] == 2


def test_scada_setpoint_write_applies_before_the_next_scan():
    rt = make_runtime(COUNTER)
    rt.run()
    rt.cycle()
    rt.write_mw(10, [55])
    assert rt.read("MW", 10, 1) == [55]      # pending writes read back at once
    rt.cycle()
    assert rt.image.qw[0] == 55


def test_load_swaps_task_and_resets_setpoints_to_defaults():
    rt = make_runtime(COUNTER)
    rt.run()
    rt.write_mw(10, [40])
    rt.cycle()
    assert rt.image.qw[0] == 40
    other = program("    sp AT %QW0 : INT;\n    der AT %MW10 : INT := 100;", "sp := der / 2;")
    rt.load(compile_task(other), {"task": "half"})
    assert rt.state == RUN and rt.task.name == "half"
    rt.cycle()
    assert rt.image.qw[0] == 50


def test_field_inputs_reach_the_task():
    src = program("    a AT %IW0 : INT;\n    ok AT %IX0.0 : BOOL;\n    y AT %QW5 : INT;", "IF ok THEN y := a; END_IF;")
    rt = make_runtime(src)
    rt.run()
    rt.field_write("IW", 0, [123])
    rt.field_write("IX", 0, [True])
    rt.cycle()
    assert rt.read("QW", 5, 1) == [123]


def test_state_reports_scan_statistics():
    rt = Runtime("plc-x", "generic-iec", clock_ms=Clock())
    assert rt.status()["task"] is None and rt.status()["state"] == STOP
    rt.load(compile_task(COUNTER), {"task": "counter", "title": "Counter"})
    rt.run()
    rt.cycle()
    st = rt.status()
    assert st["task"]["name"] == "counter" and st["scan"]["count"] == 1 and st["scan"]["last_ms"] is not None
