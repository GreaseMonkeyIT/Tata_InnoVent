"""Material flow of the two lines (ideas.md 11.7 "each station feeds the next"; 2026-10-15).

Before this, each station ran its own fixed clock, so the plant showed exact machine periods that no
real line has (plant/verify_plant.py found 19 such tags). Now a station starts when its part has
arrived from upstream and an operator has handled it.

Rules (one for every station, no station gets its own):
  - Operator handling (loading, placing a tool, rigging an engine) is a log-normal time with the
    station's mean and one coefficient of variation for all manual work, HANDLING_CV. Project choice:
    no time-study source was found; 0.3 sits in the "low variability" class of Hopp and Spearman,
    Factory Physics (CV under 0.75). Machine programs (robot seams, a test schedule, a wash cycle,
    a CED deposition) stay exact, as on a real line.
  - Release: a new frame (or engine) enters the line when the work in process is under a cap
    (CONWIP, Factory Physics ch. 10), so the slowest station sets the pace and the buffers stay
    finite. Frame line: pierce-1 is the bottleneck (Hörmann Automotive: punching is the bottleneck of
    side member lines). Engine line: hot-1.
  - Heavy parts move by crane: each transfer asks one of the bay's cranes for a lift.

The frame line (one frame = 2 side members + 6 cross members, 3 of them welded):
  side members: pierce-1 -> lmp-1 (U forming) -> blast-1 -> ced-1
  cross members: plasma-1 (brackets) -> press-1 (bends) and press-2 (gussets) -> weld-1 / weld-2
                 -> blast-1 -> ced-1
  a painted frame set -> rivet-1 (ladder assembly) -> qa-scanner-1 -> conveyor-1 (out)
The engine line: washer-1 -> part cooling -> leak-1 -> pallet-1 -> tight-1 -> tight-2 -> cold-1
  -> hot-1 (out)
"""
import math
import random

HANDLING_CV = 0.30


def handling_s(rng, mean_s, cv=HANDLING_CV):
    """A log-normal operator time with this mean and coefficient of variation."""
    s2 = math.log(1.0 + cv * cv)
    return rng.lognormvariate(math.log(mean_s) - 0.5 * s2, math.sqrt(s2))


class _Wait:
    """A handling delay that ends in an action."""
    __slots__ = ("left", "action")

    def __init__(self, left, action):
        self.left, self.action = left, action


class _Flow:
    def __init__(self, plant, seed, name):
        self.p = plant
        self.rng = random.Random(f"flow:{name}:{seed}")
        self.waits = []

    def after(self, mean_s, action):
        self.waits.append(_Wait(handling_s(self.rng, mean_s), action))

    def _tick_waits(self, dt):
        done = []
        for w in self.waits:
            w.left -= dt
            if w.left <= 0.0:
                done.append(w)
        for w in done:
            self.waits.remove(w)
            w.action()

    def lift(self, cranes, mass_t):
        free = min(cranes, key=lambda c: (c.busy, len(c.queue)))
        free.request(mass_t)


class FrameFlow(_Flow):
    # Mean handling times, s (project choices): a side member onto the punch or the press by crane,
    # a plate onto the cutting table, a bend or a stroke by hand, a cross member into a weld fixture,
    # a load hung on the CED carrier, the side and cross members into the riveting jig, a tool placed
    # on a fastener, the frame onto the scanner bed.
    H_MEMBER, H_PLATE, H_BEND, H_STROKE, H_FIXTURE = 180.0, 300.0, 25.0, 6.0, 120.0
    H_HANG, H_JIG, H_FASTENER, H_SCAN = 300.0, 600.0, 4.0, 120.0
    WIP_CAP = 3                  # frames in the line (project choice)
    BENDS, STROKES, WELDED, CROSS = 12, 24, 3, 6     # per frame (project choices)
    BLAST_M_MIN = 3.0            # roller conveyor speed through blast-1 (project choice)
    BLAST_EXTRA_M = 2.0          # blast cabinet length added to the part length (project choice)
    SCAN_S = 300.0               # frame dimensional scan (project choice)
    CROSS_KG = 50.0              # one cross member (project choice: 6 x 50 kg + 2 x 271 kg = 842 kg)
    CROSS_M2 = 0.8               # paint area of one cross member (project choice)

    def __init__(self, plant, member, seed=0):
        super().__init__(plant, seed, "frame")
        self.m = member
        self.wip = 0
        self.frames_done = 0
        self.q_pierce = []           # side members waiting: holes each
        self.q_form = self.q_blast = 0
        self.side_ready = self.cross_ready = 0      # parts blasted, waiting for CED
        self.plates_queue = self.bends_left = self.strokes_left = 0
        self.weld_queue = 0
        self.ced_sets = 0            # painted frame sets waiting for riveting
        self.scan_queue = 0
        self.out_queue = 0
        self._seen = {}
        self._pierce_loading = self._form_loading = self._blast_loading = False
        self._plasma_loading = self._ced_loading = self._rivet_loading = self._scan_loading = False
        self._weld_loading = [False, False]
        self._press_member = None
        self._tool_wait = [False, False]
        self._blast_kind = None

    def _new(self, key, value):
        old = self._seen.get(key, value)
        self._seen[key] = value
        return value - old

    def step(self, dt):
        p = self.p
        self._tick_waits(dt)
        cranes = p.cranes
        # release a frame
        while self.wip < self.WIP_CAP:
            self.wip += 1
            for _ in range(2):
                self.q_pierce.append(self.rng.randint(self.m.holes_min, self.m.holes_max))
            self.plates_queue += 1
        # side members: pierce-1
        pc = p.pierce
        self.q_form += self._new("pierced", pc.members)
        if not pc.busy and self.q_pierce and not self._pierce_loading:
            self._pierce_loading = True
            holes = self.q_pierce.pop(0)
            self.lift(cranes, self.m.mass_kg / 1000.0)
            self.after(self.H_MEMBER, lambda h=holes: (pc.begin(h), setattr(self, "_pierce_loading", False)))
        # lmp-1 forms one member per stroke; the PLC starts it after the member is placed
        lmp = p.lmp
        self.q_blast += self._new("formed", lmp.bends)
        if lmp.phase == lmp.IDLE and self.q_form > 0 and not self._form_loading and not lmp.auto:
            self._form_loading = True
            self.q_form -= 1
            self.lift(cranes, self.m.mass_kg / 1000.0)
            self.after(self.H_MEMBER, lambda: (setattr(lmp, "auto", True), setattr(lmp, "handling_s", 0.0),
                                                setattr(self, "_form_loading", False)))
        if lmp.auto and lmp.phase != lmp.IDLE:
            lmp.auto = False             # one stroke per member
        # cross members: plasma-1 cuts the bracket plate set of a frame
        pl = p.plasma
        if self._new("plates", pl.plates):
            self.bends_left += self.BENDS
            self.strokes_left += self.STROKES
            self.weld_queue += self.WELDED
            self._cross_pressed = getattr(self, "_cross_pressed", 0) + self.CROSS - self.WELDED
        if pl.state == "load" and not pl.go and self.plates_queue > 0 and not self._plasma_loading:
            self._plasma_loading = True
            self.plates_queue -= 1
            self.after(self.H_PLATE, lambda: (setattr(pl, "go", True), setattr(self, "_plasma_loading", False)))
        # press-1 bends and press-2 strokes, one handling each (the press PLC waits handling_s)
        pb = p.press
        if self._new("bends", pb.bends):
            self.bends_left = max(0, self.bends_left - 1)
        pb.auto = self.bends_left > 0
        if pb.phase == pb.IDLE and pb.t_phase < dt * 1.5:
            pb.handling_s = handling_s(self.rng, self.H_BEND)
        p2 = p.press2
        if self._new("strokes", p2.strokes):
            self.strokes_left = max(0, self.strokes_left - 1)
        p2.auto = self.strokes_left > 0
        if p2.state == p2.IDLE and p2.t_state < dt * 1.5:
            p2.handling_s = handling_s(self.rng, self.H_STROKE)
        # weld cells: a cross member once its bends and gussets of that frame are done
        for k, w in enumerate(p.welders):
            if self._new(f"weld{k}", w.parts):
                self._cross_welded = getattr(self, "_cross_welded", 0) + 1
            if (w.state == "change" and not w.go and self.weld_queue > 0 and not self._weld_loading[k]
                    and self.bends_left == 0 and self.strokes_left == 0):
                self._weld_loading[k] = True
                self.weld_queue -= 1
                self.after(self.H_FIXTURE, lambda w=w, k=k: (setattr(w, "go", True),
                                                             self._weld_loading.__setitem__(k, False)))
        # cross members of a frame go to blast as one pass when all six are ready
        welded = getattr(self, "_cross_welded", 0)
        pressed = getattr(self, "_cross_pressed", 0)
        if welded >= self.WELDED and pressed >= self.CROSS - self.WELDED and self.bends_left == 0:
            self._cross_welded = welded - self.WELDED
            self._cross_pressed = pressed - (self.CROSS - self.WELDED)
            self.q_cross_blast = getattr(self, "q_cross_blast", 0) + 1
        # blast-1: a side member or a frame's cross members per pass
        bl = p.blast
        if self._new("blasted", bl.parts):
            if self._blast_kind == "side":
                self.side_ready += 1
            elif self._blast_kind == "cross":
                self.cross_ready += 1
            self._blast_kind = None
        if not bl.busy and not self._blast_loading and self._blast_kind is None:
            kind, length = None, 0.0
            if self.q_blast > 0:
                kind, length = "side", self.m.length_m
                self.q_blast -= 1
            elif getattr(self, "q_cross_blast", 0) > 0:
                kind, length = "cross", 1.0
                self.q_cross_blast -= 1
            if kind:
                self._blast_loading, self._blast_kind = True, kind
                pass_s = (length + self.BLAST_EXTRA_M) / self.BLAST_M_MIN * 60.0
                self.after(self.H_MEMBER if kind == "side" else self.H_FIXTURE,
                           lambda s=pass_s: (bl.begin(s), setattr(self, "_blast_loading", False)))
        # ced-1: one frame set per load (2 side members + the cross members)
        ced = p.ced
        self.ced_sets += self._new("ced", ced.loads)
        if not ced.busy and not self._ced_loading and self.side_ready >= 2 and self.cross_ready >= 1:
            self._ced_loading = True
            self.side_ready -= 2
            self.cross_ready -= 1
            kg = 2 * self.m.mass_kg + self.CROSS * self.CROSS_KG
            area = 2 * self.m.area_m2 + self.CROSS * self.CROSS_M2
            self.lift(cranes, kg / 1000.0)
            self.after(self.H_HANG, lambda kg=kg, a=area: (ced.begin(kg, a), setattr(self, "_ced_loading", False)))
        # rivet-1: the ladder, fastener by fastener with two tools
        rv = p.rivet
        if self._new("riveted", rv.frames):
            self.scan_queue += 1
        if not rv.busy and self.ced_sets > 0 and not self._rivet_loading:
            self._rivet_loading = True
            self.ced_sets -= 1
            self.lift(cranes, (2 * self.m.mass_kg) / 1000.0)
            self.after(self.H_JIG, lambda: (rv.begin(), setattr(self, "_rivet_loading", False)))
        for k in range(rv.r.tools):
            if rv.left > 0 and rv.tool_free(k) and not self._tool_wait[k]:
                self._tool_wait[k] = True
                self.after(self.H_FASTENER, lambda k=k: (rv.swage(k), self._tool_wait.__setitem__(k, False)))
        # qa-scanner-1, then conveyor-1 takes the frame out
        sc = p.scanner
        if self._new("scanned", sc.parts):
            self.out_queue += 1
        if not sc.busy and self.scan_queue > 0 and not self._scan_loading:
            self._scan_loading = True
            self.scan_queue -= 1
            self.lift(cranes, 0.85)
            self.after(self.H_SCAN, lambda: (sc.begin(self.SCAN_S), setattr(self, "_scan_loading", False)))
        cv = p.conv
        if self._new("moved", cv.moves):
            self.frames_done += 1
            self.wip -= 1
        if cv.state == cv.IDLE and self.out_queue > 0:
            self.out_queue -= 1
            cv.send(2 * self.m.mass_kg + self.CROSS * self.CROSS_KG)


class EngineFlow(_Flow):
    # Mean handling times, s (project choices): a block into the washer, the block onto a pallet after
    # the leak test, an engine onto the cold-test stand, an engine rigged in the hot-test cell.
    H_WASH, H_PALLET, H_COLD, H_HOT = 120.0, 90.0, 90.0, 300.0
    WIP_CAP = 6                  # engines in the line (project choice; TS 5 pallets in the loop)
    COOL_TRANSFER_S = 580.0      # washer to leak test with the part cooling station (plant.py)

    def __init__(self, plant, seed=0):
        super().__init__(plant, seed, "engine")
        self.wip = 0
        self.engines_done = 0
        self.wash_queue = 0
        self.cooling = []            # [t_left, part temperature at the washer]
        self.leak_queue = 0
        self.stations = ["tight-1", "tight-2", "cold-1", "hot-1"]
        self.at = {s: False for s in self.stations}      # an engine at the station
        self.done_at = {s: False for s in self.stations}
        self.waiting = {s: False for s in self.stations}
        self.on_pallet = 0           # engines on the pallet line before tight-1
        self._seen = {}
        self._loading_wash = False
        self._moving = None

    def _new(self, key, value):
        old = self._seen.get(key, value)
        self._seen[key] = value
        return value - old

    def step(self, dt):
        p = self.p
        self._tick_waits(dt)
        while self.wip < self.WIP_CAP:
            self.wip += 1
            self.wash_queue += 1
        w = p.washer
        if self._new("washed", w.parts):
            self.cooling.append([self.COOL_TRANSFER_S, w.t_part_out])
        if not w.busy and self.wash_queue > 0 and not self._loading_wash:
            self._loading_wash = True
            self.wash_queue -= 1
            self.after(self.H_WASH, lambda: (w.begin(), setattr(self, "_loading_wash", False)))
        for c in self.cooling:
            c[0] -= dt
        arrived = [c for c in self.cooling if c[0] <= 0.0]
        for c in arrived:
            self.cooling.remove(c)
            p._leak_test(c[1])
            self.after(self.H_PALLET, lambda: setattr(self, "on_pallet", self.on_pallet + 1))
        # station states
        tg = {"tight-1": p.tight[0], "tight-2": p.tight[1], "cold-1": p.cold, "hot-1": p.hot}
        for s, m in tg.items():
            if self.at[s] and not m.busy and not self.waiting[s]:
                self.done_at[s] = True
        pal = p.pallet
        # one pallet move at a time: from the last station backwards, a finished engine moves on
        if self._moving is not None:
            if pal.move_left <= 0.0:
                src, dst = self._moving
                self._moving = None
                if src is not None:
                    self.at[src], self.done_at[src] = False, False
                else:
                    self.on_pallet -= 1
                if dst is None:
                    self.engines_done += 1
                    self.wip -= 1
                else:
                    self.at[dst] = True
                    self._start(dst, tg[dst])
        if self._moving is None and not pal.jam:
            order = [("hot-1", None), ("cold-1", "hot-1"), ("tight-2", "cold-1"), ("tight-1", "tight-2"),
                     (None, "tight-1")]
            for src, dst in order:
                ready = (self.on_pallet > 0) if src is None else self.done_at[src]
                free = True if dst is None else not self.at[dst]
                if ready and free:
                    self._moving = (src, dst)
                    pal.move()
                    break

    def _start(self, s, m):
        h = {"tight-1": None, "tight-2": None, "cold-1": self.H_COLD, "hot-1": self.H_HOT}[s]
        if h is None:
            m.begin()
            return
        self.waiting[s] = True
        if s == "cold-1":
            m.rigging = True                   # the PLC sees the engine on the stand while it is rigged
        self.after(h, lambda: (m.begin(), self.waiting.__setitem__(s, False)))

    # The sections of pallet-1 (2026-10-28): 8 sections of a TS 5 loop (TS5_LINE). Project choice for the
    # layout: 1 loading (after leak-1), 2 tight-1, 3 tight-2, 4 cold-1, 5 hot-1, 6 unloading, 7 and 8 the
    # return of the empty pallets. A section's PLC state: move (a pallet travels on it), hold (a pallet
    # waits at its stop gate), free, or jam (the drive slips under a stuck pallet).
    SECTION_OF = {None: 1, "tight-1": 2, "tight-2": 3, "cold-1": 4, "hot-1": 5}

    def sections(self):
        """The state of each of pallet-1's 8 sections."""
        p, out = self.p, ["free"] * 8
        if self.on_pallet > 0:
            out[0] = "hold"
        for s, k in self.SECTION_OF.items():
            if s is not None and self.at[s]:
                out[k - 1] = "hold"
        if self._moving is not None and (p.pallet.moving or p.pallet.jam):
            src, dst = self._moving
            a = self.SECTION_OF[src]
            b = self.SECTION_OF[dst] if dst is not None else 8
            for k in range(a, b + 1):
                out[k - 1] = "jam" if p.pallet.jam else "move"
        return out
