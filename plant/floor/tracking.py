"""The line tracking system of the plant sim (2026-10-18): what read points and station controllers
report about the parts.

A real line does not see a part move. It knows a part at known places only: a station's controller
knows that a part is loaded and which program runs, an RFID reader or a code scanner at a station
reads the ID on the part or its carrier, a zone sensor reports a zone as occupied, a crane's load cell
reports a load on the hook, and the tracking table of the line (in the line PLC or the MES) moves an
ID to the next station when that station reports it. This module is that tracking table for the
plant sim. Each tick it reads the plant flow (flow.py) and the station states and records:

  - an ID for each frame order, side member, cross member kit, cross member, CED load, finished
    frame, engine, CED carrier and engine pallet
  - each read: an ID at a place, with the time
  - each transfer: the ID, from, to, by what (crane-1, crane-2, hand, roller conveyor, pallet line)

It changes nothing in the plant: it makes no random draws and writes no plant value. One hook wraps
the flow's crane request (`_Flow.lift`) to note which crane took the request; the wrapper calls the
original method with the same arguments. Test: tracking on and off give the same plant tags.

Rules where the flow keeps counts and not identities (the tracking table needs one rule each):
  - first in, first out at every buffer (the flow's queues are first in, first out)
  - the kit of a frame is cut on plasma-1, bent on press-1 and punched on press-2 (its bends and
    strokes in the flow's counts, first kit first), then 3 of its 6 cross members are welded
  - ced-1 hangs 2 blasted side members and 1 blasted cross member set on one carrier: the first two
    side members and the first set in the buffer (the hang station marries their IDs to the carrier)
  - the frame takes the frame number of its right side member (the number is stamped on the side
    member before forming, as frame makers do)
Place names: "<station>" in the station, "<station>:in" its input buffer, "<station>:out" its output
buffer, "crane-<k>" on the hook, "ced-1:<stage>" in a CED tank, "pallet-1:<station>" on a pallet at
a station stop.
"""

CED_CARRIERS = 6          # carriers (flight bars) of the CED line (project choice)


class Tracker:
    def __init__(self, plant, year=26):
        self.p = plant
        self.f = plant.fflow
        self.e = plant.eflow
        self.year = year
        self.events = []          # [t, kind, id, src, dst, info]
        self.where = {}           # id -> place
        self.items = {}           # id -> {"type": ...}
        self._seen = {}
        self.t = 0.0
        self.late_lifts = 0       # crane lifts that started after their part had left the pick-up place
        self.unmatched_lifts = 0
        # frame line buffers (first in, first out)
        self.side_stock, self.plate_stock = [], []
        self.pierce_out, self.form_out = [], []
        self.kits_press = []      # [kit, bends left, strokes left]
        self.kits_weld = []       # [kit, welds to start, welds done]
        self.welded = []          # welded cross members, waiting to make a set
        self.sent_sets = set()
        self.cross_blast = []
        self.blast_side, self.blast_cross = [], []
        self.ced_out, self.scan_in, self.out_in = [], [], []
        self.oven = []            # CED loads in the cure oven
        self.on = {}              # station -> the ID in it
        self.weld_part = {}       # weld cell -> cross member ID
        self.carriers = {f"C{k:02d}": None for k in range(1, CED_CARRIERS + 1)}
        self.n_frame = self.n_load = self.n_engine = 0
        self.q_cross_taken = 0
        self._ced_obj = None
        self._ced_place = None
        self._conv_item = None
        # cranes: requests in the order the flow made them, and the job on the hook
        self._lift_log = []
        self.crane_jobs = {c.name: [] for c in plant.cranes}
        self.crane_job = {c.name: None for c in plant.cranes}
        if self.f is not None:
            orig = self.f.lift

            def lift(cranes, mass_t, _orig=orig):
                before = [c.lifts + len(c.queue) for c in cranes]
                _orig(cranes, mass_t)
                after = [c.lifts + len(c.queue) for c in cranes]
                k = next((i for i, (a, b) in enumerate(zip(before, after)) if b > a), 0)
                self._lift_log.append(cranes[k].name)
            self.f.lift = lift
        # engine line
        self.wash_in, self.cooling, self.leak_out = [], [], []
        self.pallets = {f"P{k:02d}": None for k in range(1, (self.e.WIP_CAP if self.e else 0) + 1)}
        self.pallet_at = {}       # pallet -> station stop or "load"
        self.on_pallet_q = []     # pallets with an engine, waiting before tight-1
        self._move = None         # (engine, from, to) of the pallet move in progress
        self._mv_obj = None       # the flow's move object of that move

    # ------------------------------------------------------------------------ helpers --
    def _rise(self, key, value):
        """The rise of a counter since the last tick. Counters start at 0 with the plant, and the
        tracker starts with the plant, so the first tick counts from 0."""
        old = self._seen.get(key, 0)
        self._seen[key] = value
        return value - old

    def _went(self, key, value):
        """(was, now) of a flag or a state; a flag is off before the first tick."""
        old = self._seen.get(key, False)
        self._seen[key] = value
        return old, value

    def _ev(self, kind, item, src, dst, info=None):
        self.events.append([round(self.t, 1), kind, item, src, dst, info or {}])
        if kind in ("new", "move"):
            self.where[item] = dst
        elif kind in ("join", "ship"):
            self.where.pop(item, None)

    def _new(self, item, typ, place, **kw):
        self.items[item] = {"type": typ, **kw}
        self._ev("new", item, None, place)

    def _crane(self, item, src, dst):
        """A transfer by crane: the request goes to the crane the flow chose; the item stays at its
        place until the crane hoists it."""
        name = self._lift_log.pop(0) if self._lift_log else None
        if name is None:
            self._ev("move", item, src, dst, {"by": "hand"})
            return
        self.crane_jobs[name].append([item, src, dst])
        self._ev("request", item, src, dst, {"by": name})

    def _arrive(self, item, place, info=None):
        src = self.where.get(item)
        if src != place:
            self._ev("move", item, src, place, info)

    # ------------------------------------------------------------------------ step --
    def step(self, t):
        self.t = t
        if self.f is not None:
            self._frame_line()
            if self._lift_log:               # a crane request with no tracked part (never expected)
                self.unmatched_lifts += len(self._lift_log)
                self._lift_log.clear()
            self._cranes()
        if self.e is not None:
            self._engine_line()

    def _frame_id(self, n):
        return f"FR{self.year}-{n:04d}"

    def _frame_line(self):
        p, f = self.p, self.f
        # releases: a frame order puts 2 side members on the stock rack and a plate set at plasma-1
        released = f.frames_done + f.wip
        for _ in range(self._rise("released", released)):
            self.n_frame += 1
            fr = self._frame_id(self.n_frame)
            self.items[fr] = {"type": "order"}
            for side in ("R", "L"):
                sm = f"{fr}-{side}"
                self._new(sm, "side member", "pierce-1:in", frame=fr)
                self.side_stock.append(sm)
            kit = f"{fr}-K"
            self._new(kit, "kit", "plasma-1:in", frame=fr)
            self.plate_stock.append(kit)
        self._seen["released"] = released
        # pierce-1 (by crane)
        was, now = self._went("pierce_loading", f._pierce_loading)
        if now and not was and self.side_stock:
            sm = self.side_stock.pop(0)
            self.on["pierce-1"] = sm
            self._crane(sm, "pierce-1:in", "pierce-1")
        if was and not now and self.on.get("pierce-1"):
            # the CNC program of the member: its hole count, and the hole counter at the start (the
            # member's place along the line follows the holes done, as the CNC's own axis does)
            self._arrive(self.on["pierce-1"], "pierce-1", {"read": "rfid", "holes_total": p.pierce.holes_left,
                                                           "holes_at": p.pierce.holes})
        if self._rise("pierce_members", p.pierce.members) > 0 and self.on.get("pierce-1"):
            sm = self.on.pop("pierce-1")
            self._arrive(sm, "pierce-1:out", {"holes": p.pierce.holes})
            self.pierce_out.append(sm)
        # lmp-1 (by crane)
        was, now = self._went("form_loading", f._form_loading)
        if now and not was and self.pierce_out:
            sm = self.pierce_out.pop(0)
            self.on["lmp-1"] = sm
            self._crane(sm, "pierce-1:out", "lmp-1")
        if was and not now and self.on.get("lmp-1"):
            self._arrive(self.on["lmp-1"], "lmp-1", {"read": "rfid"})
        if self._rise("lmp_bends", p.lmp.bends) > 0 and self.on.get("lmp-1"):
            sm = self.on.pop("lmp-1")
            self._arrive(sm, "lmp-1:out")
            self.form_out.append(sm)
        # plasma-1 (by hand: the plate set goes onto the table)
        was, now = self._went("plasma_loading", f._plasma_loading)
        if now and not was and self.plate_stock:
            kit = self.plate_stock.pop(0)
            self.on["plasma-1"] = kit
            self._ev("move", kit, "plasma-1:in", "plasma-1", {"by": "hand"})
        if self._rise("plates", p.plasma.plates) > 0 and self.on.get("plasma-1"):
            kit = self.on.pop("plasma-1")
            self._arrive(kit, "press-1:in", {"by": "hand"})
            self.kits_press.append([kit, f.BENDS, f.STROKES])
        # press-1 bends and press-2 strokes, first kit first; the kit shows at the press that still
        # has work on it (bends first)
        for idx, n in ((1, self._rise("p1_bends", p.press.bends)), (2, self._rise("p2_strokes", p.press2.strokes))):
            for _ in range(max(0, n)):
                k = next((k for k in self.kits_press if k[idx] > 0), None)
                if k is None:
                    break
                k[idx] -= 1
        for k in list(self.kits_press):
            if k[1] == 0 and k[2] == 0:
                self.kits_press.remove(k)
                self._arrive(k[0], "weld:in")
                self.kits_weld.append([k[0], f.WELDED, 0])
            elif k[1] < f.BENDS or k[2] < f.STROKES:
                self._arrive(k[0], "press-1" if k[1] > 0 else "press-2")
        # weld cells (by hand into the fixture), cross members first kit first
        for i, w in enumerate(p.welders):
            was, now = self._went(f"weld_loading{i}", f._weld_loading[i])
            if now and not was:
                k = next((k for k in self.kits_weld if k[1] > 0), None)
                if k is not None:
                    k[1] -= 1
                    cm = f"{k[0]}-X{f.WELDED - k[1]}"
                    self.items[cm] = {"type": "cross member", "kit": k[0]}
                    self.weld_part[w.name] = cm
                    self._ev("move", cm, "weld:in", w.name, {"by": "hand"})
            if self._rise(f"weld_parts{i}", w.parts) > 0 and w.name in self.weld_part:
                cm = self.weld_part.pop(w.name)
                self.welded.append(cm)
                self._arrive(cm, "weld:out")
        # A cross member set goes to blast when 3 welds are done (the flow counts welds over all
        # kits: the first 3 welded cross members make the set, with the pressed cross members of the
        # kit of the first one).
        # Sets made by the flow = sets blast took + sets waiting. Blast may take one in this tick (its
        # block comes below), so count that take here already.
        q = getattr(f, "q_cross_blast", 0)
        taking = f._blast_loading and not self._seen.get("blast_loading", False) and f._blast_kind == "cross"
        total = self.q_cross_taken + int(taking) + q
        for _ in range(max(0, total - self._seen.get("qcb_total", 0))):
            members = [self.welded.pop(0) for _ in range(min(f.WELDED, len(self.welded)))]
            # the set takes the kit of its first member whose kit has not gone to blast yet
            kits = [self.items[m]["kit"] for m in members]
            kit = next((k for k in kits if k not in self.sent_sets), kits[0] if kits else None)
            if kit is None and self.kits_weld:
                kit = self.kits_weld[0][0]
            if kit is None:
                break
            if kit in self.sent_sets:
                n = 2
                while f"{kit}{n}" in self.items:
                    n += 1
                self.items[f"{kit}{n}"] = {"type": "kit", "frame": self.items[kit].get("frame")}
                kit = f"{kit}{n}"
                self._ev("new", kit, None, "weld:in")
            self.sent_sets.add(kit)
            for cm in members:
                self._ev("join", cm, "weld:out", None, {"into": kit})
            self.items[kit]["welded"] = members
            self.kits_weld = [k for k in self.kits_weld if k[0] != kit or k[1] > 0]
            self._arrive(kit, "blast-1:in")
            self.cross_blast.append(kit)
        self._seen["qcb_total"] = total
        # blast-1: one side member or one cross member set per pass, on the roller conveyor
        was, now = self._went("blast_loading", f._blast_loading)
        if now and not was:
            kind = f._blast_kind
            src = self.form_out if kind == "side" else self.cross_blast
            if kind == "cross":
                self.q_cross_taken += 1
            if src:
                item = src.pop(0)
                self.on["blast-1"] = item
                self._arrive(item, "blast-1:infeed", {"by": "roller conveyor"})
        if was and not now and self.on.get("blast-1"):
            self._arrive(self.on["blast-1"], "blast-1", {"pass_s": round(p.blast.blast_left, 1)})
        if self._rise("blasted", p.blast.parts) > 0 and self.on.get("blast-1"):
            item = self.on.pop("blast-1")
            self._arrive(item, "ced-1:in")
            (self.blast_cross if self.items[item]["type"] == "kit" else self.blast_side).append(item)
        # ced-1: the hang station marries 2 side members and 1 cross member set to a carrier
        was, now = self._went("ced_loading", f._ced_loading)
        if now and not was and len(self.blast_side) >= 2 and self.blast_cross:
            parts = [self.blast_side.pop(0), self.blast_side.pop(0), self.blast_cross.pop(0)]
            self.n_load += 1
            load = f"L{self.n_load:04d}"
            carrier = next((c for c, v in self.carriers.items() if v is None), None)
            if carrier:
                self.carriers[carrier] = load
            self.items[load] = {"type": "CED load", "parts": parts, "carrier": carrier}
            for x in parts:
                self._ev("join", x, "ced-1:in", None, {"into": load})
            self._ev("new", load, None, "ced-1:in", {"carrier": carrier, "parts": parts})
            self.on["ced-1"] = load
            self._crane(load, "ced-1:in", "ced-1:degrease")
        ced = p.ced
        if ced.load is not None and ced.load is not self._ced_obj:
            self._ced_obj = ced.load
            load = self.on.pop("ced-1", None)
            self._ced_load = load
            self._ced_place = None
        if ced.load is not None and getattr(self, "_ced_load", None):
            ld = ced.load
            stage = ced.stages[ld["i"]]
            place = f"ced-1:{stage}"
            if ld["moving"]:
                place = f"ced-1:hoist>{stage}"
            if place != self._ced_place:
                self._ced_place = place
                self._arrive(self._ced_load, place, {"by": "hoist"} if ld["moving"] else {"read": "hoist"})
        if ced.load is None and self._ced_obj is not None:
            self._ced_obj = None
            if getattr(self, "_ced_load", None):
                self.oven.append(self._ced_load)
                self._arrive(self._ced_load, "ced-1:oven", {"by": "hoist"})
                self._ced_load = None
        for _ in range(max(0, self._rise("ced_loads", ced.loads))):
            if self.oven:
                load = self.oven.pop(0)
                c = self.items[load]["carrier"]
                if c:
                    self.carriers[c] = None
                self._arrive(load, "ced-1:out", {"carrier free": c})
                self.ced_out.append(load)
        # rivet-1 (by crane into the jig)
        was, now = self._went("rivet_loading", f._rivet_loading)
        if now and not was and self.ced_out:
            load = self.ced_out.pop(0)
            self.on["rivet-1"] = load
            self._crane(load, "ced-1:out", "rivet-1")
        if was and not now and self.on.get("rivet-1"):
            self._arrive(self.on["rivet-1"], "rivet-1", {"read": "scan"})
        if self._rise("riveted", p.rivet.frames) > 0 and self.on.get("rivet-1"):
            load = self.on.pop("rivet-1")
            right = next((x for x in self.items[load]["parts"] if x.endswith("-R")), self.items[load]["parts"][0])
            frame = right[:-2]
            self.items[frame] = {"type": "frame", "load": load, "parts": self.items[load]["parts"]}
            self._ev("join", load, "rivet-1", None, {"into": frame})
            self._ev("new", frame, None, "rivet-1:out", {"from": load})
            self.scan_in.append(frame)
        # qa-scanner-1 (by crane)
        was, now = self._went("scan_loading", f._scan_loading)
        if now and not was and self.scan_in:
            fr = self.scan_in.pop(0)
            self.on["qa-scanner-1"] = fr
            self._crane(fr, "rivet-1:out", "qa-scanner-1")
        if was and not now and self.on.get("qa-scanner-1"):
            self._arrive(self.on["qa-scanner-1"], "qa-scanner-1", {"read": "scan"})
        if self._rise("scanned", p.scanner.parts) > 0 and self.on.get("qa-scanner-1"):
            fr = self.on.pop("qa-scanner-1")
            self._arrive(fr, "conveyor-1:in")
            self.out_in.append(fr)
        # conveyor-1 takes the frame out (its position: the conveyor's own travel, x)
        cv = p.conv
        was, now = self._went("conv_state", cv.state)
        if now == cv.MOVING and was != cv.MOVING and self.out_in:
            self._conv_item = self.out_in.pop(0)
            self._arrive(self._conv_item, "conveyor-1", {"by": "roller conveyor"})
        if self._rise("conv_moves", cv.moves) > 0 and self._conv_item:
            self._ev("ship", self._conv_item, "conveyor-1", None)
            self._conv_item = None

    def _cranes(self):
        for c in self.p.cranes:
            started = self._rise(f"lifts:{c.name}", c.lifts)
            for _ in range(max(0, started)):
                if self.crane_jobs[c.name]:
                    item, src, dst = self.crane_jobs[c.name].pop(0)
                    if self.where.get(item) != src:
                        self.late_lifts += 1          # the station had started without the crane
                        self.crane_job[c.name] = None
                    else:
                        self.crane_job[c.name] = [item, src, dst]
                        self._ev("move", item, src, c.name, {"by": c.name, "to": dst})
            job = self.crane_job[c.name]
            if job and c.phase in ("return", "idle"):
                item, src, dst = job
                if self.where.get(item) == c.name:
                    self._ev("move", item, c.name, dst, {"by": c.name})
                self.crane_job[c.name] = None

    def _engine_line(self):
        p, e = self.p, self.e
        released = e.engines_done + e.wip
        for _ in range(self._rise("e_released", released)):
            self.n_engine += 1
            eng = f"EN{self.year}-{self.n_engine:04d}"
            self._new(eng, "engine", "washer-1:in")
            self.wash_in.append(eng)
        self._seen["e_released"] = released
        was, now = self._went("wash_loading", e._loading_wash)
        if now and not was and self.wash_in:
            eng = self.wash_in.pop(0)
            self.on["washer-1"] = eng
            self._ev("move", eng, "washer-1:in", "washer-1:load", {"by": "hand"})
        if was and not now and self.on.get("washer-1"):
            self._arrive(self.on["washer-1"], "washer-1", {"read": "scan"})
        if self._rise("washed", p.washer.parts) > 0 and self.on.get("washer-1"):
            eng = self.on.pop("washer-1")
            self._arrive(eng, "cool-station")
            self.cooling.append(eng)
        n_tested = self._rise("leak_tested", p.leak.tested)
        for _ in range(max(0, n_tested)):
            if self.cooling:
                eng = self.cooling.pop(0)
                self._arrive(eng, "leak-1", {"decay_pa": round(p.leak.last_decay_pa, 1),
                                             "ok": bool(p.leak.last_ok)})
                self.leak_out.append(eng)
        # A pallet move: the flow keeps one move object per move. A new object (or none) ends the last
        # move, in the flow's own tick (the flow can end one move and start the next in one tick).
        mv = e._moving
        from_load = 0
        if mv is not self._mv_obj and self._move is not None:
            eng, src, dst = self._move
            self._move = None
            if src is None:
                from_load = 1
                if self.on_pallet_q:
                    self.on_pallet_q.pop(0)
            else:
                self.on.pop(f"st:{src}", None)
            if eng:
                if dst is None:
                    pal = self.items[eng].get("pallet")
                    if pal:
                        self.pallets[pal] = None
                    self._ev("ship", eng, "pallet-1:transfer", None, {"pallet free": pal})
                else:
                    self.on[f"st:{dst}"] = eng
                    self._arrive(eng, f"pallet-1:{dst}", {"read": "rfid", "pallet": self.items[eng].get("pallet")})
        # onto a pallet after the leak test (by hand): the flow adds 1 to on_pallet per engine and
        # takes 1 at the end of a move from the load stop
        added = e.on_pallet - self._seen.get("on_pallet", 0) + from_load
        self._seen["on_pallet"] = e.on_pallet
        for _ in range(max(0, added)):
            if self.leak_out:
                eng = self.leak_out.pop(0)
                pal = next((k for k, v in self.pallets.items() if v is None), None)
                if pal:
                    self.pallets[pal] = eng
                self.items[eng]["pallet"] = pal
                self._ev("move", eng, "leak-1", "pallet-1:load", {"by": "hand", "pallet": pal})
                self.on_pallet_q.append(eng)
        if mv is not self._mv_obj:
            self._mv_obj = mv
            if mv is None:
                return
            src, dst = mv
            eng = self.on_pallet_q[0] if src is None and self.on_pallet_q else self.on.get(f"st:{src}")
            self._move = (eng, src, dst)
            if eng:
                self._ev("move", eng, f"pallet-1:{src or 'load'}", "pallet-1:transfer",
                         {"by": "pallet line", "to": f"pallet-1:{dst or 'unload'}",
                          "pallet": self.items[eng].get("pallet"), "move_s": round(p.pallet.move_s, 1)})

    # ------------------------------------------------------------------------ checks --
    def check(self):
        """Conservation: every part is in one place, joined into another part, or shipped, and never
        two of these."""
        out = []
        ends = {}
        for ev in self.events:
            if ev[1] in ("join", "ship"):
                ends.setdefault(ev[2], []).append(ev[1])
        for item, info in self.items.items():
            if info["type"] in ("order", "cross member") and item not in self.where and item not in ends:
                continue                  # an order is not a part; a cross member exists from its weld on
            n = (item in self.where) + len(ends.get(item, []))
            if n != 1:
                out.append(f"{item}: {'no place' if n == 0 else 'placed and ended, or ended twice'}")
        if any(v is not None and v not in self.items for v in self.carriers.values()):
            out.append("a carrier holds an unknown load")
        return out
