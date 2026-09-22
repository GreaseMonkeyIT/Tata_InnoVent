"""The Structured Text subset (FLEET.md 5.1): function blocks, types, statements, and compile errors."""
import os

import pytest

from conftest import REPO_ROOT, Prog, program
from image import Image
from st import CompileError, STRuntimeError, compile_task


def set_local(p, name, value):
    p.inst.vals[p.task.symbols[name.upper()].slot] = value


def test_ton_on_delay_with_injected_clock():
    p = Prog(program("    go : BOOL := TRUE;\n    t : TON;\n    q : BOOL;\n    et : TIME;",
                     "t(IN := go, PT := T#500ms);\nq := t.Q;\net := t.ET;"))
    p.scan(0)
    assert p["q"] is False
    p.scan(400)
    assert p["q"] is False and p["et"] == 400
    p.scan(100)
    assert p["q"] is True and p["et"] == 500
    set_local(p, "go", False)
    p.scan(10)
    assert p["q"] is False and p["et"] == 0


def test_tof_holds_q_after_the_input_falls():
    p = Prog(program("    go : BOOL := TRUE;\n    t : TOF;\n    q : BOOL;", "t(IN := go, PT := T#1s);\nq := t.Q;"))
    p.scan(0)
    assert p["q"] is True
    set_local(p, "go", False)
    p.scan(10).scan(500)
    assert p["q"] is True
    p.scan(600)
    assert p["q"] is False


def test_tp_pulse_ignores_the_input_during_the_pulse():
    p = Prog(program("    go : BOOL;\n    t : TP;\n    q : BOOL;", "t(IN := go, PT := T#300ms);\nq := t.Q;"))
    p.scan(0)
    assert p["q"] is False
    set_local(p, "go", True)
    p.scan(10)
    assert p["q"] is True
    set_local(p, "go", False)
    p.scan(200)
    assert p["q"] is True
    p.scan(200)
    assert p["q"] is False


def test_counters_and_edges():
    p = Prog(program("    clk : BOOL;\n    rst : BOOL;\n    up : CTU;\n    re : R_TRIG;\n    fe : F_TRIG;\n"
                     "    rq : BOOL;\n    fq : BOOL;",
                     "up(CU := clk, R := rst, PV := 3);\nre(CLK := clk);\nfe(CLK := clk);\nrq := re.Q;\nfq := fe.Q;"))
    for _ in range(3):
        set_local(p, "clk", True)
        p.scan(10)
        assert p["rq"] is True
        p.scan(10)
        assert p["rq"] is False
        set_local(p, "clk", False)
        p.scan(10)
        assert p["fq"] is True
    assert p["up"].CV == 3 and p["up"].Q is True
    set_local(p, "rst", True)
    p.scan(10)
    assert p["up"].CV == 0 and p["up"].Q is False


def test_ctd_and_bistables():
    p = Prog(program("    load : BOOL := TRUE;\n    cd : BOOL;\n    down : CTD;\n    s : BOOL := TRUE;\n    r : BOOL := TRUE;\n"
                     "    sr1 : SR;\n    rs1 : RS;\n    srq : BOOL;\n    rsq : BOOL;",
                     "down(CD := cd, LD := load, PV := 2);\nsr1(S1 := s, R := r);\nrs1(S := s, R1 := r);\n"
                     "srq := sr1.Q1;\nrsq := rs1.Q1;"))
    p.scan(0)
    assert p["down"].CV == 2
    assert p["srq"] is True and p["rsq"] is False          # SR: set wins, RS: reset wins
    set_local(p, "load", False)
    for _ in range(2):
        set_local(p, "cd", True)
        p.scan(10)
        set_local(p, "cd", False)
        p.scan(10)
    assert p["down"].CV == 0 and p["down"].Q is True


def test_int_wraps_and_integer_division_truncates():
    p = Prog(program("    a : INT := 32767;\n    b : INT;\n    c : INT := -7;\n    d : INT;\n    e : INT;",
                     "b := a + 1;\nd := c / 2;\ne := c MOD 2;"))
    p.scan(0)
    assert p["b"] == -32768
    assert p["d"] == -3 and p["e"] == -1


def test_case_for_and_functions():
    p = Prog(program("    sel : INT := 2;\n    out : INT;\n    i : INT;\n    sum : INT;\n    lim : INT;\n    r : REAL;",
                     "CASE sel OF\n  1: out := 10;\n  2, 3: out := 20;\nELSE\n  out := 99;\nEND_CASE;\n"
                     "FOR i := 1 TO 4 DO\n  sum := sum + i;\nEND_FOR;\n"
                     "lim := LIMIT(0, 150, 100);\nr := INT_TO_REAL(sum) / 2.0;"))
    p.scan(0)
    assert p["out"] == 20 and p["sum"] == 10 and p["lim"] == 100 and p["r"] == 5.0


def test_compile_error_carries_line_and_column():
    src = "PROGRAM t\n  VAR\n    a : INT;\n  END_VAR\n  a := TRUE;\nEND_PROGRAM\n"
    with pytest.raises(CompileError) as ei:
        compile_task(src)
    assert ei.value.line == 5 and ei.value.col >= 1
    assert "BOOL" in str(ei.value)


def test_system_words_are_reserved_for_the_runtime():
    with pytest.raises(CompileError) as ei:
        compile_task(program("    x AT %MW3 : INT;", "x := 1;"))
    assert "system word" in str(ei.value)


def test_task_cannot_write_an_input():
    with pytest.raises(CompileError):
        compile_task(program("    x AT %IW0 : INT;", "x := 1;"))


def test_division_by_zero_is_a_runtime_error():
    p = Prog(program("    a : INT := 1;\n    z : INT;\n    b : INT;", "b := a / z;"))
    with pytest.raises(STRuntimeError):
        p.scan(0)


def test_configuration_interval_is_read():
    src = program("    a : INT;", "a := a + 1;") + (
        "CONFIGURATION c\n  RESOURCE r ON PLC\n    TASK fast(INTERVAL := T#50ms, PRIORITY := 0);\n"
        "    PROGRAM p WITH fast : t;\n  END_RESOURCE\nEND_CONFIGURATION\n")
    assert compile_task(src).interval_ms == 50


def test_openplc_trip_program_parity():
    """plc/program.st latches at 780 (78.0 C x10) and unlatches only below trip on a reset."""
    with open(os.path.join(REPO_ROOT, "plc", "program.st"), encoding="utf-8") as f:
        task = compile_task(f.read(), reserve_system_words=False)
    img = Image()
    task.apply_initial_values(img)
    inst = task.instantiate(img)
    img.mw[0] = 790                                     # press-1 at 79.0 C
    inst.scan(0)
    assert img.qx[0] is True
    img.mw[0] = 700
    inst.scan(100)
    assert img.qx[0] is True                            # latched, no reset yet
    img.mw[20] = 1
    inst.scan(200)
    assert img.qx[0] is False and img.mw[20] == 0       # reset consumed
    img.mw[0], img.mw[1] = 800, 800
    inst.scan(300)
    img.mw[0], img.mw[20] = 700, 1                      # press-2 still hot at reset time
    inst.scan(400)
    assert img.qx[0] is False and img.qx[1] is True
