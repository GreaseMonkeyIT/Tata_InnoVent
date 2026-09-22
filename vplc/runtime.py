"""The scan engine of the virtual PLC (FLEET.md section 6).

One cycle:
  1. copy the field inputs (%IX, %IW) and the external %MW writes into the working image
  2. run the program if the state is RUN (the scan time stamp comes from clock_ms)
  3. write the system words %MW0..%MW4
  4. publish a copy of the image to the protocol buffers

The runtime measures steps 1 and 2 with perf_counter. That is the scan time in %MW0 and /state.
A scan longer than the task interval counts as an overrun.

STOP sets every %QX and %QW to 0. A runtime error in the task sets FAULT: the outputs go to 0,
%MW2 = 2, and /state reports the error. The loop keeps doing steps 1, 3, and 4 in STOP and FAULT,
so SCADA still sees live inputs and the state word.

Thread safety: _scan_lock covers a whole cycle and every control action (load, run, stop). A
control action therefore happens at a scan boundary. _io covers the buffers that the protocol
servers touch. The lock order is always _scan_lock, then _io.
"""
import collections
import threading
import time

from image import N_BITS, N_WORDS, SYSTEM_WORDS, Image, wrap16

VERSION = "0.1.0"
STOP, RUN, FAULT = "STOP", "RUN", "FAULT"
STATE_CODE = {STOP: 0, RUN: 1, FAULT: 2}
AVG_WINDOW = 100


class NoTaskError(Exception):
    """RUN was requested but no task is loaded."""


class LoadedTask:
    """A compiled task plus its manifest (FLEET.md 5.2)."""

    def __init__(self, compiled, manifest):
        self.compiled = compiled
        self.manifest = manifest or {}

    @property
    def name(self):
        return self.manifest.get("task") or self.compiled.program_name.lower()

    @property
    def title(self):
        return self.manifest.get("title") or self.name

    def info(self):
        return {"name": self.name, "title": self.title, "sha256": self.compiled.sha256,
                "interval_ms": self.compiled.interval_ms}


class Runtime:
    def __init__(self, name="plc-local", profile="generic-iec", clock_ms=None, perf=None, wall=None):
        self.name = name
        self.profile = profile
        self.clock_ms = clock_ms or (lambda: int(time.monotonic() * 1000))
        self.perf = perf or time.perf_counter
        self.wall = wall or time.time
        self.started_at = self.wall()

        self.image = Image()                  # working image, owned by the cycle
        self._scan_lock = threading.RLock()
        self._io = threading.Lock()
        self._field_ix = [False] * N_BITS     # the latest field port writes
        self._field_iw = [0] * N_WORDS
        self._pending_mw = {}                 # SCADA writes waiting for the next cycle
        self._published = Image()
        self._listeners = []

        self.state = STOP
        self.task = None
        self.instance = None
        self.fault = None
        self.denied_writes = 0
        self.scan_count = 0
        self.overruns = 0
        self.last_ms = None
        self.max_ms = None
        self._window = collections.deque(maxlen=AVG_WINDOW)
        self._ever_ran = False
        self.on_event = None                  # callable(kind), kind is "first_run" or "load"

        self._halt = threading.Event()
        self._thread = None

    # ----------------------------------------------------------------- control --
    def interval_ms(self):
        return self.task.compiled.interval_ms if self.task else 100

    def load(self, compiled, manifest=None):
        """Swap in a compiled task at a scan boundary. The declared initial values apply, so setpoints
        return to the task defaults. RUN stays RUN, STOP stays STOP, FAULT becomes STOP."""
        with self._scan_lock:
            self.task = LoadedTask(compiled, manifest)
            self._reset_image_for_task()
            with self._io:
                self._pending_mw.clear()
            if self.state == FAULT:
                self.state = STOP
                self.fault = None
            self._finish_cycle()
        self._emit("load")

    def run(self):
        """Enter RUN. From FAULT the task restarts from its initial values."""
        with self._scan_lock:
            if self.task is None:
                raise NoTaskError("no task is loaded")
            if self.state == FAULT:
                self._reset_image_for_task()
                self.fault = None
            self.state = RUN
            first = not self._ever_ran
            self._ever_ran = True
            self._finish_cycle()
        if first:
            self._emit("first_run")

    def stop(self):
        """Enter STOP. Every %QX and %QW goes to 0 now."""
        with self._scan_lock:
            if self.state != FAULT:
                self.state = STOP
            self.image.clear_outputs()
            self._finish_cycle()

    def boot_fault(self, message):
        """Record a boot failure (for example a task that does not compile). No task runs."""
        with self._scan_lock:
            self.state = FAULT
            self.fault = message
            self.image.clear_outputs()
            self._finish_cycle()

    def _reset_image_for_task(self):
        img = self.image
        img.clear_outputs()
        for i in range(SYSTEM_WORDS, N_WORDS):
            img.mw[i] = 0
        self.task.compiled.apply_initial_values(img)
        self.instance = self.task.compiled.instantiate(img)

    def _emit(self, kind):
        cb = self.on_event
        if cb is not None:
            try:
                cb(kind)
            except Exception as e:                                  # enrollment is never fatal
                print(f"vplc: event hook failed for {kind}: {e}", flush=True)

    # ------------------------------------------------------------------- cycle --
    def cycle(self):
        """Run one full cycle. The scan loop calls this once per interval. Tests call it directly."""
        with self._scan_lock:
            now = self.clock_ms()
            t0 = self.perf()
            img = self.image
            with self._io:
                img.ix[:] = self._field_ix
                img.iw[:] = self._field_iw
                for i, v in self._pending_mw.items():
                    img.mw[i] = v
                self._pending_mw.clear()
            ran = False
            if self.state == RUN and self.instance is not None:
                ran = True
                try:
                    self.instance.scan(now)
                except Exception as e:
                    self._set_fault(e)
            if ran:
                elapsed = (self.perf() - t0) * 1000.0
                self._record_scan(elapsed)
            self._finish_cycle()

    def _set_fault(self, exc):
        from st import STRuntimeError
        if isinstance(exc, STRuntimeError):
            msg = f"runtime error at {exc}"
        else:
            msg = f"internal error: {type(exc).__name__}: {exc}"
        self.state = FAULT
        self.fault = msg
        print(f"vplc {self.name}: FAULT, {msg}", flush=True)

    def _record_scan(self, elapsed_ms):
        with self._io:
            self.scan_count += 1
            self.last_ms = elapsed_ms
            self.max_ms = elapsed_ms if self.max_ms is None else max(self.max_ms, elapsed_ms)
            self._window.append(elapsed_ms)
            if elapsed_ms > self.interval_ms():
                self.overruns += 1

    def _finish_cycle(self):
        if self.state != RUN:
            self.image.clear_outputs()
        self._write_system_words()
        self._publish()

    def _write_system_words(self):
        mw = self.image.mw
        last = self.last_ms
        mw[0] = 0 if last is None else min(int(round(last * 100)), 32767)
        mw[1] = self.scan_count % 32768
        mw[2] = STATE_CODE[self.state]
        mw[3] = self.overruns % 32768
        mw[4] = self.task.compiled.crc15 if self.task else 0

    def _publish(self):
        with self._io:
            pub = self.image.copy()
            for i, v in self._pending_mw.items():
                pub.mw[i] = v
            self._published = pub
            listeners = list(self._listeners)
        for fn in listeners:
            fn(pub)

    # --------------------------------------------------------------- scan loop --
    def start(self):
        self._halt.clear()
        self._thread = threading.Thread(target=self._loop, name=f"scan-{self.name}", daemon=True)
        self._thread.start()

    def halt(self, timeout=5.0):
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _loop(self):
        next_t = self.perf()
        while not self._halt.is_set():
            now = self.perf()
            if now < next_t:
                self._halt.wait(min(next_t - now, 0.25))
                continue
            try:
                self.cycle()
            except Exception as e:                                  # keep the loop alive
                print(f"vplc {self.name}: cycle error {type(e).__name__}: {e}", flush=True)
            next_t += self.interval_ms() / 1000.0
            now = self.perf()
            if next_t < now:
                next_t = now

    # ------------------------------------------------------- protocol buffers --
    def add_publish_listener(self, fn):
        """fn(published_image) runs after every publish, outside the _io lock."""
        with self._io:
            self._listeners.append(fn)
            pub = self._published
        fn(pub)

    def read(self, area, start, count):
        """Read the published image. %MW includes writes that wait for the next cycle."""
        with self._io:
            values = self._published.area(area)[start:start + count]
            if area == "MW" and self._pending_mw:
                for k in range(count):
                    if start + k in self._pending_mw:
                        values[k] = self._pending_mw[start + k]
            return values

    def write_mw(self, start, values):
        """External %MW writes (SCADA). They apply before the next scan."""
        with self._io:
            for k, v in enumerate(values):
                self._pending_mw[start + k] = wrap16(v)

    def queue_mw(self, words):
        """Same as write_mw for a dict {index: value}."""
        with self._io:
            for i, v in words.items():
                self._pending_mw[i] = wrap16(v)

    def pending_mw(self):
        with self._io:
            return dict(self._pending_mw)

    def field_write(self, area, start, values):
        """The field port writes %IX (bools) or %IW (words). They apply at the next scan."""
        with self._io:
            if area == "IX":
                for k, v in enumerate(values):
                    self._field_ix[start + k] = bool(v)
            elif area == "IW":
                for k, v in enumerate(values):
                    self._field_iw[start + k] = wrap16(v)
            else:
                raise ValueError(f"the field port cannot write {area}")

    def field_read(self, area, start, count):
        """The field port reads back its own inputs, or the published outputs."""
        with self._io:
            if area == "IX":
                return self._field_ix[start:start + count]
            if area == "IW":
                return self._field_iw[start:start + count]
        return self.read(area, start, count)

    def count_denied(self, n=1):
        with self._io:
            self.denied_writes += n

    # ------------------------------------------------------------------ status --
    def status(self):
        with self._io:
            avg = sum(self._window) / len(self._window) if self._window else None
            return {
                "name": self.name,
                "profile": self.profile,
                "state": self.state,
                "task": self.task.info() if self.task else None,
                "scan": {
                    "last_ms": None if self.last_ms is None else round(self.last_ms, 4),
                    "avg_ms": None if avg is None else round(avg, 4),
                    "max_ms": None if self.max_ms is None else round(self.max_ms, 4),
                    "count": self.scan_count,
                    "overruns": self.overruns,
                },
                "started_at": self.started_at,
                "fault": self.fault,
                "denied_writes": self.denied_writes,
            }
