"""Protection relays shared by the machines (ideas.md 11.3 "Protection"; carried over from the
LOG-100 sim with the same sources, SCENARIOS.md section 12).

Overload: an IEC 60947-4-1 class 10 thermal overload relay. Thermal model
    theta' = ((I / Ir)^2 - theta) / tau,  trip and latch at theta >= 1.125^2
With tau = 283 s it does not trip at 1.05 x Ir, trips at 1.2 x Ir within the 2 h limit (about
10 min), at 1.5 x Ir in about 234 s (class 10 limit 240 s) and at 7.2 x Ir in about 7 s (class 10:
4 to 10 s from cold). No random numbers.

Undervoltage: ANSI device 27. A stage trips when the voltage stays below its share of the rating
for its delay. The trip clears above the reset share. The stages (90 % for 10 s, 85 % for 2 s) and
the 95 % reset are typical settings (project choice within the guidance: longer than a motor-start
dip).
"""

OL_TAU_S = 283.0
OL_TRIP_THETA = 1.125 ** 2
UV_STAGES = ((0.85, 2.0), (0.90, 10.0))
UV_RESET_FRAC = 0.95


class Overload:
    def __init__(self, i_set_a, tau_s=OL_TAU_S, trip_theta=OL_TRIP_THETA):
        self.i_set, self.tau_s, self.trip_theta = i_set_a, tau_s, trip_theta
        self.theta = 0.0
        self.latched = False

    def step(self, amps, dt):
        """Integrate one step of motor current (exact for a held current). True while tripped."""
        import math
        x = (amps / self.i_set) ** 2 if self.i_set > 0 else 0.0
        self.theta = x + (self.theta - x) * math.exp(-dt / self.tau_s)
        if self.theta >= self.trip_theta:
            self.latched = True
        return self.latched

    def ratio(self):
        return min(1.0, self.theta / self.trip_theta)

    def reset(self):
        """A manual reset closes the relay. The thermal memory stays (a bimetal is still warm)."""
        self.latched = False


class Undervoltage:
    def __init__(self, stages=UV_STAGES, reset_frac=UV_RESET_FRAC):
        self.stages, self.reset_frac = tuple(stages), reset_frac
        self.below = [0.0] * len(self.stages)
        self.tripped = False

    def step(self, frac, dt):
        for i, (limit, delay) in enumerate(self.stages):
            self.below[i] = self.below[i] + dt if frac < limit else 0.0
            if self.below[i] >= delay:
                self.tripped = True
        if self.tripped and frac >= self.reset_frac:
            self.tripped = False
            self.below = [0.0] * len(self.stages)
        return self.tripped
