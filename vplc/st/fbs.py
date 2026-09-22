"""The standard function blocks of IEC 61131-3: TON TOF TP CTU CTD R_TRIG F_TRIG SR RS.

Each block keeps its inputs between calls, as the standard requires. A call sets the given
inputs and then runs execute(now_ms). Timers use now_ms from the scan, never the wall clock,
so a test can drive them with an injected clock.

The logic follows the standard's reference bodies. One consequence is worth knowing: F_TRIG
starts with M = FALSE, so its first call with CLK = FALSE gives Q = TRUE for one call.
"""

INT_MIN, INT_MAX = -32768, 32767


class FB:
    INPUTS = {}
    OUTPUTS = {}

    def execute(self, now_ms):
        raise NotImplementedError


class TON(FB):
    """On-delay: Q goes TRUE when IN has been TRUE for PT. ET counts up to PT."""
    INPUTS = {"IN": "BOOL", "PT": "TIME"}
    OUTPUTS = {"Q": "BOOL", "ET": "TIME"}

    def __init__(self):
        self.IN, self.PT, self.Q, self.ET = False, 0, False, 0
        self._running, self._start = False, 0

    def execute(self, now_ms):
        if self.IN:
            if not self._running:
                self._running, self._start = True, now_ms
            et = now_ms - self._start
            if et >= self.PT:
                self.Q, self.ET = True, self.PT
            else:
                self.Q, self.ET = False, et
        else:
            self._running = False
            self.Q, self.ET = False, 0


class TOF(FB):
    """Off-delay: Q follows IN up at once and drops PT after IN falls. ET counts the delay."""
    INPUTS = {"IN": "BOOL", "PT": "TIME"}
    OUTPUTS = {"Q": "BOOL", "ET": "TIME"}

    def __init__(self):
        self.IN, self.PT, self.Q, self.ET = False, 0, False, 0
        self._prev_in, self._timing, self._start = False, False, 0

    def execute(self, now_ms):
        if self.IN:
            self.Q, self.ET = True, 0
            self._timing = False
        else:
            if self._prev_in:
                self._timing, self._start = True, now_ms
            if self._timing:
                et = now_ms - self._start
                if et >= self.PT:
                    self.Q, self.ET = False, self.PT
                    self._timing = False
                else:
                    self.Q, self.ET = True, et
        self._prev_in = self.IN


class TP(FB):
    """Pulse: a rising edge of IN starts a pulse of length PT. Edges during the pulse are ignored."""
    INPUTS = {"IN": "BOOL", "PT": "TIME"}
    OUTPUTS = {"Q": "BOOL", "ET": "TIME"}

    def __init__(self):
        self.IN, self.PT, self.Q, self.ET = False, 0, False, 0
        self._prev_in, self._state, self._start = False, "idle", 0

    def execute(self, now_ms):
        if self._state == "idle" and self.IN and not self._prev_in:
            self._state, self._start = "pulse", now_ms
        if self._state == "pulse":
            et = now_ms - self._start
            if et >= self.PT:
                self._state = "done"
                self.Q, self.ET = False, self.PT
            else:
                self.Q, self.ET = True, et
        if self._state == "done":
            self.Q = False
            if self.IN:
                self.ET = self.PT
            else:
                self._state, self.ET = "idle", 0
        if self._state == "idle":
            self.Q = False
        self._prev_in = self.IN


class CTU(FB):
    """Up counter: a rising edge of CU adds 1 to CV. R resets CV to 0. Q = CV >= PV."""
    INPUTS = {"CU": "BOOL", "R": "BOOL", "PV": "INT"}
    OUTPUTS = {"Q": "BOOL", "CV": "INT"}

    def __init__(self):
        self.CU, self.R, self.PV, self.Q, self.CV = False, False, 0, False, 0
        self._prev = False

    def execute(self, now_ms):
        rising = self.CU and not self._prev
        self._prev = self.CU
        if self.R:
            self.CV = 0
        elif rising and self.CV < INT_MAX:
            self.CV += 1
        self.Q = self.CV >= self.PV


class CTD(FB):
    """Down counter: a rising edge of CD takes 1 from CV. LD loads PV into CV. Q = CV <= 0."""
    INPUTS = {"CD": "BOOL", "LD": "BOOL", "PV": "INT"}
    OUTPUTS = {"Q": "BOOL", "CV": "INT"}

    def __init__(self):
        self.CD, self.LD, self.PV, self.Q, self.CV = False, False, 0, False, 0
        self._prev = False

    def execute(self, now_ms):
        rising = self.CD and not self._prev
        self._prev = self.CD
        if self.LD:
            self.CV = self.PV
        elif rising and self.CV > INT_MIN:
            self.CV -= 1
        self.Q = self.CV <= 0


class R_TRIG(FB):
    """Rising edge detector: Q is TRUE for one call when CLK goes from FALSE to TRUE."""
    INPUTS = {"CLK": "BOOL"}
    OUTPUTS = {"Q": "BOOL"}

    def __init__(self):
        self.CLK, self.Q, self._m = False, False, False

    def execute(self, now_ms):
        self.Q = self.CLK and not self._m
        self._m = self.CLK


class F_TRIG(FB):
    """Falling edge detector: Q := NOT CLK AND NOT M, M := NOT CLK (the standard's body)."""
    INPUTS = {"CLK": "BOOL"}
    OUTPUTS = {"Q": "BOOL"}

    def __init__(self):
        self.CLK, self.Q, self._m = False, False, False

    def execute(self, now_ms):
        self.Q = (not self.CLK) and (not self._m)
        self._m = not self.CLK


class SR(FB):
    """Set-dominant bistable: Q1 := S1 OR (NOT R AND Q1)."""
    INPUTS = {"S1": "BOOL", "R": "BOOL"}
    OUTPUTS = {"Q1": "BOOL"}

    def __init__(self):
        self.S1, self.R, self.Q1 = False, False, False

    def execute(self, now_ms):
        self.Q1 = self.S1 or ((not self.R) and self.Q1)


class RS(FB):
    """Reset-dominant bistable: Q1 := NOT R1 AND (S OR Q1)."""
    INPUTS = {"S": "BOOL", "R1": "BOOL"}
    OUTPUTS = {"Q1": "BOOL"}

    def __init__(self):
        self.S, self.R1, self.Q1 = False, False, False

    def execute(self, now_ms):
        self.Q1 = (not self.R1) and (self.S or self.Q1)


FB_TYPES = {cls.__name__: cls for cls in (TON, TOF, TP, CTU, CTD, R_TRIG, F_TRIG, SR, RS)}
