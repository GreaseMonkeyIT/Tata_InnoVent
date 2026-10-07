# The VISR Book

**Tata InnoVent 2026 · Team SiliconKnights**
**Category §3.2.2.5: Edge AI for Connected, Secure and Intelligent Industrial Systems**

This book explains VISR from end to end, in plain words. It says what problem we attack, how every part
of the system works, where each idea came from, what we have proved on real hardware, and where we want
to take it next. You do not need to read the code to follow it. Where the code matters, the book names
the file, so an engineer can check every claim.

**Reading paths**

- **Five minutes:** chapters 1, 2 and 3. They give the problem, the idea, and one fault from start to finish.
- **For a judge or a reviewer:** add chapter 8 (how VISR decides), chapter 15 (the seven scenarios),
  chapter 19 (how VISR compares) and chapter 21 (our honesty rules).
- **For an engineer:** read Part III in full, then Appendix B for the map of the repo.

---

## Contents

**Part I · Why**
1. The problem: plants fail between machines
2. The idea on one page

**Part II · One fault, end to end**
3. Scenario 1: from a sticky bearing to a measured fix

**Part III · How it is built**
4. The shape of the system
5. The plant: physics, not scripts
6. The controllers: OpenPLC and the virtual PLC fleet
7. The data path: SCADA, the historian, and the five-second window
8. The engine: how VISR decides what caused what
9. Forecasts: a warning before the trip
10. Integrity: when a report breaks the physics
11. The narrator: a local language model that only speaks
12. Acting safely: the act loop
13. Security and trust
14. The console

**Part IV · Proof**
15. The seven scenarios
16. How we test and prove it
17. What went wrong, and what it taught us

**Part V · Where the ideas come from**
18. Our inspirations
19. How VISR compares

**Part VI · Where we are going**
20. What we intend to achieve
21. Our honesty rules

**Appendices**
A. Glossary
B. Where things live in the repo
C. The numbers we measured

---

# Part I · Why

## 1. The problem: plants fail between machines

A modern plant is full of sensors. Almost every machine can tell you its own temperature, its own
current and its own speed. Almost every machine also has alarms for its own limits. So when one machine
goes wrong, the plant rarely lacks data. It lacks an explanation.

The hard failures are the ones that travel. One machine pulls too much power, and the voltage drops for
every machine on the same supply. One pump weakens, and every machine on the same coolant loop starts to
warm. One chatty device floods a network segment, and every controller on that segment starts to lag.
Each affected machine raises its own alarm. The operator sees twenty red lights, and the one light that
matters looks like all the others.

History shows how this ends.

- **Milford Haven refinery, UK, 1994.** A valve fault kept pumps filling a closed vessel. In the last
  11 minutes before the explosion, two operators faced 275 alarms. Twenty-six people were injured. The
  investigation became a classic case study in alarm floods.
- **LG Polymers, Visakhapatnam, 2020.** The cooling of a styrene tank did not run around the clock, and
  the tank had no temperature sensor at the top. The contents heated with nobody seeing the trend. At
  least twelve people died.
- **Toyota, Japan, 2023.** A full server disk stopped production at 12 of 14 plants in Japan, about
  13,000 cars a day. The backup system failed the same way. The monitoring system itself was the weak
  point.
- **Jaguar Land Rover, 2025.** A cyberattack stopped production for about five weeks. The estimated cost
  to the UK economy was about 1.9 billion pounds. Security is now a production problem, not an IT one.

The money is large too. Siemens estimates that unplanned downtime costs the Fortune Global 500 about
1.4 trillion dollars a year, about 11 % of their revenue, and that an hour of downtime in an automotive
plant costs about 2.3 million dollars.

So the question we asked was simple: **when a fault lives between machines, who explains it?**

## 2. The idea on one page

VISR is a small causal-AI system that runs on one computer at the edge of a plant, with no cloud. It
watches the plant and it watches itself. When something goes wrong, it answers four questions:

1. **What caused it?** VISR names one root cause, not twenty symptoms.
2. **What is the evidence?** It shows why it believes that: which signal moved first, and which physical
   connection carried the effect.
3. **What happens next?** It forecasts a trip or a crash before it happens, with a time estimate.
4. **What can we safely do?** It offers one small, bounded action. A human confirms it, the system
   carries it out through the real control protocol, and then it measures whether the action helped.

It also watches for one more thing: **a report that breaks the physics.** If a controller claims a
current that the feeder meter does not see, or a setpoint changes with no signed record, VISR treats it
as a security event.

The sentence we use to describe what makes VISR different is this one:

> **Everyone detects anomalies. We explain them, across sources, with evidence.**

The key idea behind that sentence is the **shared-medium rule**. Two signals can move together for many
reasons. VISR only accepts that one machine caused a problem in another when the two share something
physical: a power rail, a coolant loop, a network segment, a controller, or a computer. Correlation
becomes a causal link only along a wire, a pipe, or a shared resource. That one rule removes most of the
false alarms that plague correlation-based tools.

The name comes from the visor mode of the Halo games, a heads-up display that highlights what matters
in a noisy scene. That is the job: make the one important thing visible.

---

# Part II · One fault, end to end

## 3. Scenario 1: from a sticky bearing to a measured fix

The best way to understand VISR is to follow one fault through the whole system. This is Scenario 1,
the rail-sag cascade, as it ran on our edge box on 22 September 2026.

This chapter keeps the numbers of that run. On 26 September a realism pass changed the plant (chapter 5):
the rails now sag 3 to 4 % at full load instead of 10 %, each machine trips at its own limit (80 °C for
the presses and cnc-1, 55 °C for the furnace coil water), and four virtual PLCs run the plant. The story
is the same. The numbers below are the ones from 22 September.

### The calm plant

Our plant has eight machines. Four of them (press-1, press-2, cnc-1 and qa-scanner-1) share one power
rail, called rail A. Four others (conveyor-1, compressor-1, furnace-1 and chiller-1) share rail B. A
third rail, rail C, is a spare feeder for new machines. Four machines are water-cooled through one
shared coolant loop, which chiller-1 drives.

Two kinds of controller run this plant. A virtual PLC called `plc-stamping` runs the stamping line. It
commands press-1 and press-2 through its field port, and it reports to SCADA over the Siemens S7comm
protocol. A real OpenPLC runtime acts as the safety interlock: if a cooled machine reaches 78 °C,
OpenPLC trips it, and the machine's contactor opens.

Before any demo, the system has watched this plant for hours. It has learned the normal band of every
signal. It knows, for example, that compressor-1 switches on for about a minute every five minutes and
pulls rail B down while it runs. That is normal, and VISR must never call it a fault. On the console the
verdict panel says **STEADY**, and it has said so for every minute of the soak.

### One small change

We fire scenario 1 from the fault shell on the box (`deploy/faults.sh`). The operator console has no
fault button, as in a real plant: the operator sees only the effects. The only thing that changes is
one number inside the plant model: the bearing friction of press-1 rises by a factor of 1.9. Nothing else is scripted. We never
write "voltage low" anywhere.

### The physics does the rest

- press-1 now needs more current to do the same work. Its current rises from about 43 A to about 85 A.
- Rail A has a small internal resistance, like every real supply. The rail voltage is the supply voltage
  minus the total current times that resistance. More current means a lower voltage. Rail A sags from
  about 361 V to about 344 V. Every machine on rail A feels it.
- cnc-1 and qa-scanner-1 are voltage-sensitive. Under the lower voltage they slow down, and their
  throughput drops from about 89 % to about 85 %. They did nothing wrong. They are victims.
- press-1 also heats up faster, because more current means more heat. Its coolant temperature starts
  to climb toward the 78 °C trip line.

On a classic system, this is the moment the alarms start: low voltage on rail A, slow cnc-1, slow
qa-scanner-1, high current on press-1, rising temperature. Five alarms, one cause.

### The signals travel

Every second, the plant model writes each machine's current, temperature, voltage and throughput into
the controllers as sensor words, exactly as field wiring would. The SCADA tag server reads the
controllers over Modbus TCP and S7comm, rates every tag GOOD, STALE or BAD by its age, and stores the
readings in a time-series historian. Prometheus collects the same signals. Every five seconds, a small
service called the aggregator builds one tidy window: every signal of every machine and every computer,
lined up on the same five-second grid.

### The engine reasons

The causal engine reads that window and runs a deterministic pass. In plain words, it does this:

1. **Notice what changed.** Rail A's voltage has left its learned band, and so has press-1's current.
2. **Wait until it is real.** A signal must stay out of band for most of two minutes before it counts.
   This is why a normal one-minute compressor run never convicts anyone.
3. **Find what moved first.** Lagged correlation shows that the voltage sag follows press-1's current,
   not the other way round. press-1's own load rose first. The console calls this the **write** evidence.
4. **Check for a shared medium.** press-1, cnc-1 and qa-scanner-1 all sit on rail A. The link is
   physically possible. The console calls this the **rail** evidence.
5. **Check the order in time.** press-1's change began before the victims' changes. That is the
   **temporal** evidence.
6. **Rank the suspects.** The engine scores each candidate by how much of the whole picture it explains.
   press-1 explains the sag and both slowdowns. Nothing upstream explains press-1.

About 80 seconds after the fault, the verdict panel changes to **ROOT CAUSE: press-1**, with three
evidence chips (write, rail, temporal) and the chain press-1 → rail A → cnc-1 and qa-scanner-1.

### A warning before the damage

The engine did not wait for the verdict to help. About 30 seconds after the fault, it had already
projected press-1's temperature ramp to the 78 °C line and shown a **trip card**: press-1 will trip in
under two minutes. A trip card is a forecast, and the console marks it as one.

### A human-sized explanation

A local language model reads the verdict and writes two sentences for the operator: press-1 is the
likely root of the rail A voltage sag, and cnc-1 and qa-scanner-1 degrade with it. The model runs on the
edge box. It only narrates. The verdict existed before it spoke, and it would exist if the model were
switched off.

### One safe action

The Actions panel now offers exactly one bounded step: **derate press-1 to 55 % through plc-stamping**.
The card cites the verdict it acts on.

1. The operator clicks **Execute** and reads the confirmation.
2. The operator clicks **Confirm and execute**.
3. The API checks that the current verdict still names press-1. If the verdict had changed in the
   meantime, the API would refuse with a 409 and write the refusal to the ledger.
4. The API writes the intent into the hash-chained audit ledger.
5. The SCADA tag server writes one setpoint, the derate percentage, into `plc-stamping` over S7comm.
6. The PLC's Structured Text program clamps the press's speed command to that setpoint.
7. The plant model reads the new speed command through the field port, and press-1 slows down.

### The measured result

press-1's current fell from 85.3 A to 45.0 A. Rail A came back from 344.4 V to 359.6 V. The victims
recovered. The trip that the forecast predicted never happened. About a minute after the write, the API
measured the relief itself and wrote it into the ledger, with the currents and voltages before and after.
The relief is measured, not claimed.

When the operator resets the fault and restores the setpoint to 100 %, those actions are signed and
recorded too.

### The same story as a timeline (edge box, 22 September 2026)

| Time after the fault | What happened |
|---|---|
| 0 s | press-1 friction × 1.9 (one parameter) |
| about 5 s | press-1 current climbs, rail A sags, victims slow |
| 30.2 s | trip card: press-1 heading for its 78 °C trip |
| 80.3 s | verdict: root press-1, evidence write + rail + temporal |
| Execute | one derate over S7comm, cited, confirmed, ledgered |
| about 60 s later | relief measured: 85.3 → 45.0 A, 344.4 → 359.6 V, no trip |

That is the whole product in one fault: **detect, explain, forecast, act with a human, and measure.**

---

# Part III · How it is built

## 4. The shape of the system

VISR has five layers and watches two planes.

```
One edge box (k3s on one computer, no cloud)

L0  Plant        plant model (physics) · OpenPLC trip interlock · virtual PLCs (S7comm, Modbus TCP)
L1  Data         SCADA tag server (48 quality-rated tags) · historian (TimescaleDB)
                 Prometheus · Linux pressure signals (PSI) · network map from eBPF
L2  Window       aggregator: one schema-stable window of every signal, every 5 seconds
L3  Engine       baselines → changepoints → lagged correlation → shared-medium gate
                 → root ranking → memory → forecasts
L4  People       API (login, audit ledger, act loop, integrity checks) · console · local narrator
```

**The two planes.**

- **The plant plane** is a physics-simulated factory. It is a model, and every screen says so. Faults
  change the model's parameters, and the symptoms emerge from the physics.
- **The edge plane** is the real computer that runs VISR. Its CPU, memory and disk pressure are real
  kernel signals, and its network map is discovered from real traffic. The same engine watches both
  planes, with the same rules. Scenario 6 happens entirely on this real plane: a service really runs out
  of memory, and the kernel really kills it.

**Why one box?** A plant cannot wait for the cloud, and many plants do not allow the cloud at all. VISR
runs on one 16-core computer with 15 GB of memory, including the language model, and needs no internet
connection. It is edge-first by design. Cloud services are optional extras for backup and scale
(chapter 20), never part of a verdict.

**The platform.** Every part runs as a container on k3s, a small Kubernetes distribution. A local image
registry on the box holds the images, so a deploy needs no internet and no administrator password. A
set of scripts in `deploy/` brings the whole stack up, checks it, and proves it (chapter 16).

## 5. The plant: physics, not scripts

We had no real factory to test on. So we built a small one out of equations. This was the most important
design decision of the project, and we made it for one reason: **a script can only show you what you
wrote into it. A model can surprise you.** When a fault in our model produces a cascade, nobody typed
that cascade. It comes out of the physics, which is also how it happens in a real plant.

The model lives in `plant/sim/main.py`. It steps once per second.

### Power: rails with a real internal resistance

Each rail has a nominal voltage of 400 V and a small source resistance. The rail voltage is:

> rail voltage = supply voltage − (total current on the rail × source resistance)

Think of a garden hose that feeds several sprinklers. When one sprinkler opens wide, the pressure drops
for all of them. That is exactly how a power sag spreads along a rail.

The source resistance is 0.12 ohm, so a rail at full normal load sits 3 to 4 % under 400 V. That is the
drop that IEC 60364-5-52 recommends for motor circuits. Before the realism pass of 26 September, rail A
idled at 360 V, 10 % low, which no plant engineer would accept. Every value of the pass comes from a
published source or is marked a project choice, and `SCENARIOS.md` section 12 lists them.

Machines react to a sag in two different ways, as real ones do:

- **Voltage-sensitive machines** (like cnc-1 and qa-scanner-1) slow down when the voltage drops.
- **Constant-power machines** keep their power steady, so they draw more current when the voltage
  drops. That extra current deepens the sag a little more.

Above all the rails sits the **supply**, the incoming board of the plant. A dip at the supply sags every
rail at once, with no machine leading. That case matters, because it is the one cause that lives above
the plant (Scenario 7 in chapter 15).

Each rail also has an independent **feeder meter**: the true sum of the currents on it. The integrity
checks compare this meter with what the controllers report (chapter 10).

### Cooling: one loop, many lags

Four machines share one coolant loop with a pump. Each cooled machine is a first-order thermal system.
It heats with the current it draws, and the coolant carries the heat away. Each machine has its own time
constant, from 90 seconds for cnc-1 to 270 seconds for furnace-1, chosen to match real equipment.

When the pump weakens, the flow drops, and every machine on the loop starts to warm. But each warms at
its own pace. That staggered response gives the engine a real lag structure to reason about, instead of
a set of identical curves.

chiller-1 cools the loop, and it can remove at most 46 kW. When the machines and the water-cooled
compressor put in more heat than that, the loop water warms, and every machine on the loop warms with
it. The chiller has the protections of a real unit: a **motor overload relay** on the IEC 60947-4-1
class 10 curve (no trip at 1.05 times its rating, a trip in about 7 seconds at 7.2 times), an
**undervoltage relay** that stops it when its supply stays low, and an **anti-recycle timer** that keeps
5 minutes between two starts.

### Duty cycles: the normal that looks like a fault

compressor-1 fills an air receiver. Its controller loads it at 6.9 bar and unloads it at 7.5 bar, so it
runs loaded for about one minute in every five. Every time it loads, it pulls rail B down and puts more
heat into the loop, because it is water-cooled. This is the hardest test for any root-cause tool, because it looks exactly like a
fault, several times an hour. VISR must stay silent through it. It does, because a deviation must hold
for most of two minutes before it counts, and because the learned baseline includes the cycle.

### The network segment

The stamping cell's controller link runs through a modelled network segment, a queue with a fixed
capacity and a small buffer. An HMI gateway talks on the same segment. When the gateway floods the
segment, the queue fills, the controller's requests start to wait, and some are dropped. The queue is a
model, but its delays act on the real Modbus requests of the cell (Scenario 3).

### New machines on demand

The operator can add a new cell of machines, for example a packaging line with a conveyor, a wrapper
and a labeler, wired to a new virtual PLC on rail C. The model adds them live, and the engine learns
the new members of rail C at run time (chapter 6).

### The honesty label

Every plant signal carries the prefix `plant_`, which means physics-simulated. The console labels the
plant floor "physics-simulated" and the inference "real". We never hide which part is a model.

## 6. The controllers: OpenPLC and the virtual PLC fleet

Real plants split control into two jobs: the process controller runs production, and a separate safety
system trips machines when a limit is crossed. VISR keeps that split.

### OpenPLC: the real trip interlock

OpenPLC is an open-source PLC runtime that runs IEC 61131-3 programs. Our trip program
(`plc/program.st`) reads every cooled machine's temperature from the plant, and when one reaches its own
trip limit, it sets that machine's trip coil. The limits are 80 °C for the press hydraulic oil and the
cnc-1 spindle motor, and 55 °C for the furnace-1 coil cooling water. The plant model reads the coil and opens the machine's contactor. The
trip **latches** until someone resets it, as a real interlock does. The plant model acts as the field
wiring: it writes sensor words into OpenPLC over Modbus TCP and reads the trip coils back.

This means the trips in our demos are not simulated. A real PLC runtime decides them.

### The virtual PLC fleet

We also built our own soft PLC runtime (`vplc/`) so that we could show a fleet of controllers coming
online. Each virtual PLC:

- runs a program written in **Structured Text**, the IEC 61131-3 language that real PLC engineers use.
  Our compiler supports assignments, IF, CASE and FOR, timers and counters (TON, TOF, TP, CTU, CTD),
  edge detectors, flip-flops, and the common math functions. A compile error reports the line and the
  column, and a failed load keeps the old program running.
- runs a measured **scan cycle**, typically every 100 ms, and reports its scan times and overruns.
- speaks a real protocol to SCADA: **S7comm** (the Siemens S7-1200 profile, data block DB1) or **Modbus
  TCP** (a generic IEC profile). These are real protocol frames, so the same SCADA drivers would read a
  real PLC.
- commands its machines through a **field port**, and the plant model reads the commands back.
- **enrolls** with SCADA using a signed token, and repeats that as a heartbeat every 30 seconds.

A virtual PLC is a protocol profile, not vendor firmware, and every card on the console says so.

Four virtual PLCs run the base plant. `plc-stamping` runs press-1 and press-2. `plc-utilities` runs
compressor-1 (its load band and a run command) and chiller-1 (a demand limit). `plc-machining` sends
cnc-1 the feed hold and the feed override, the way a line PLC talks to a CNC that keeps its own
program. `plc-furnace` sends furnace-1 its heat enable and power limit. The furnace trip stays in
OpenPLC, because NFPA 86 wants the excess-temperature limit on its own device.

### A PLC comes online in 15.6 seconds

From the console, an operator can add a PLC: pick a task (for example the packaging cell), a protocol
profile, and a rail. The system then creates the Kubernetes objects, starts the runtime, enrolls it,
waits for SCADA to read GOOD tags, and adds its machines to the engine's window. The console shows six
real timestamps for these phases. On our box, the whole sequence took 15.6 seconds, with no code change.

## 7. The data path: SCADA, the historian, and the five-second window

### The SCADA tag server

The tag server (`scada/tagserver.py`) is the plant's data backbone. It reads OpenPLC and every enrolled
virtual PLC, and turns raw words into named tags with units, addresses and a quality flag:

- **GOOD** when the value is fresh,
- **STALE** when the controller has not answered for a while,
- **BAD** when it has been silent too long. A BAD tag leaves the metrics entirely, so nothing downstream
  mistakes an old value for a new one.

The base plant has 48 tags: 30 measured and 18 calculated, such as the heat of a machine. The four
base PLCs add their own tags. The tag server also writes setpoints, but only to tags on a writable list,
only within limits, and only with the API's write token. It is the only path by which VISR changes
anything in the plant through the act loop.

### The historian

Every GOOD reading goes into a TimescaleDB table (`plant_tags`), as a real plant historian would store
it. If the historian goes down, the tag server keeps serving and simply stops counting rows until it
comes back.

### Prometheus and the kernel signals

Prometheus collects the plant signals and the edge computer's own signals. On the edge plane, the most
useful signals are Linux **pressure stall information** (PSI): the share of time that work on the box
waited for CPU, memory or disk. PSI tells you when the computer is struggling, before a service fails.
An eBPF tool called Caretta discovers which services talk to which from real network traffic, and the
console draws that map on the EDGE view.

Prometheus also records what VISR concludes. The API serves the current verdict as series: the root and
its score, the number of findings, the time left on each forecast card, the causal edges, the open
integrity findings, and any derate in force. Grafana can then show a fault and the verdict about it on
one time axis. Prometheus keeps all of this on the slow disk of the edge computer for 30 days (or
100 GB), so a restart does not erase the history (`deploy/prometheus-storage.sh`, live since
3 October 2026).

### The aggregator: one window every five seconds

The aggregator (`aggregator/`, written in Go) turns all of this into one input for the engine. Every five
seconds it produces a window: the same signal names, for every machine and every service, on the same
grid, with the same schema every time. The engine never talks to the plant or to Prometheus directly. It
only reads this window. That keeps the engine simple, testable, and easy to replay.

## 8. The engine: how VISR decides what caused what

The engine (`correlation/`) is the heart of VISR. It is deterministic statistics, not a trained neural
network. Given the same input, the same learned baselines and the same settings, it gives the same
verdict every time. There is no language model anywhere in this process. This is our Edge AI.

Here are its parts, in the order a signal meets them.

### 8.1 Baselines: learning what normal looks like

For every signal, the engine learns a normal band from recent history, using outlier-resistant
statistics (the median and the median absolute deviation) that ignore short spikes. Three rules keep the baselines honest:

- A baseline learns **only from a window that is at least 90 % full**. After a restart, the window is
  mostly empty, and learning from it would teach the engine that normal is zero. We learned this the hard
  way (chapter 17).
- Every signal family has a **floor** on its band, so a signal that barely moves does not raise an alarm
  over a tiny wobble.
- The engine **learns, then locks**. It learns during the soak, and the operator then locks the baselines.
  A locked baseline never changes, so a long fault cannot teach the engine that the fault is normal. On
  25 September a 17-minute Scenario 2 did exactly that to rail B while learning was still on, and every
  normal compressor cycle afterwards looked like an incident. A newly added machine still learns until its
  own baseline is ready.

### 8.2 The deviation gate: is it real?

A signal counts as deviating only when it stays out of band for **most of the last two minutes**. The
test uses a low quantile of the recent values, so a short spike or a one-minute compressor run cannot
pass. This is the main reason the plant stays QUIET for hours. The price is time: a real root takes about
80 seconds to confirm. We accept that price, because a confident wrong answer is worse than a slower
right one.

### 8.3 Changepoints: when did it start?

For each deviating signal, the engine finds the moment the change began, using an exponentially weighted
moving average and a two-sided CUSUM test on the residuals, a classic technique from quality control.
It also classifies the shape of the change: a step, a ramp, or a spike.

### 8.4 Lagged correlation: what moved first?

For pairs of signals, the engine measures the correlation at a range of time lags and finds the lag with
the strongest link. If the voltage sag follows press-1's current by a few seconds, then press-1's current
leads. Leading is not proof, but it is evidence.

### 8.5 The evidence gate: the rule that kills false alarms

A candidate link from A to B enters the causal graph only if **all three** of these hold:

1. **Statistical:** the correlation is strong at its best lag, and still elevated at the next lag, so it
   is not a fluke of one sample.
2. **Physical:** A and B share a declared medium. On the plant plane that is a rail, a coolant loop, a
   network segment, or a PLC cell. On the edge plane it is a shared disk or a real network dependency.
   Two services that merely feel pressure at the same moment do not count. That is coincidence, not
   coupling.
3. **Temporal:** A's change began before B's, consistently with the lag.

The shared media are declared in configuration (`deploy/engine.yaml`) and grow at run time: when a new
PLC cell comes online, its machines join its rail and its PLC domain automatically.

This gate is the part of VISR we are proudest of. It is simple enough to explain to an operator, and it
turns correlation into a claim that a physical path exists. When the console shows a **rail** chip, it
means exactly that: the link passed the physical clause along rail A.

### 8.6 Writers and victims

On a rail, the machine whose own load rose first is the likely **writer** of the disturbance. The
engine pairs each victim signal (the rail voltage) with a source signal (a machine's current), so it can
tell the aggressor from the victims. On the coolant loop, the source signals are a machine's heat load
and the loop's cooling shortfall. On the network segment, the source is the offered traffic.

### 8.7 Ranking: who explains the most?

Among the accepted links, the engine ranks each node by its **explanatory reach**: how much of the
observed trouble it explains through the links that leave it, with a decay per hop. A node that
something else explains gets a penalty. Ties go to the earliest onset. The result is a sentence an
operator can check: "press-1 explains most of the observed degradation".

Not every link may vote. On a power rail every machine sees the same bus voltage, and in the coolant
loop the temperatures are slow, so the machine that heats fastest always seems to move first. A link
between two machines on these media votes only when the leader's own load, or the lost cooling, moved
first. A plain correlation between two machines still shows on the map as context, but it never makes a
machine the root. Before this rule, cnc-1 (the fastest to heat) took the root in the coolant scenarios.
The network segment keeps plain correlations, because there each member's delay really differs.

### 8.8 Memory: learning across incidents

The pure pass above has no memory. Around it, the service keeps a small database
(`correlation/engine/state.py`) that remembers edge confidence across passes, promotes stable incidents
into **cases**, and keeps them beyond the telemetry window. One rule is strict: **memory can never
out-vote live evidence.** A remembered edge may support a verdict only on its own signal, and only while
the live data agree.

### 8.9 Many signals, one graph

The engine runs one pass per signal family (rail voltage, coolant temperature, network latency, and the
edge computer's CPU, memory and disk pressure). Each family has its own witness and its own memory.
A merge step then unites the results into one graph and ranks the root over the whole union. A machine
that disturbs two resources at once ranks higher than one that disturbs only one.

### 8.10 The common-mode rule: a cause above the plant

Sometimes every member of a medium moves together and none leads. A supply dip does that: every rail
sags at the same instant. The normal rule looks for a leading source and finds none. The common-mode
rule handles this case: when every member of a declared medium deviates together and no member leads,
the medium itself is the cause. On the plant, that means the incoming board. The evidence label
`common_mode` says that the direction comes from the declared topology, and the console does not claim
more than that. This rule is built and unit-tested, and its full scenario is still open (chapter 15).

## 9. Forecasts: a warning before the trip

A root cause after the damage is useful. A warning before the damage is better. VISR forecasts two kinds
of failure by extrapolating a trend to a known limit:

- **Thermal trips.** A coolant temperature does not climb in a straight line. After a change in heat or
  cooling it bends over and levels off, the way a kettle on a low flame stops short of boiling. The
  engine fits that curve to each cooled machine: the level it settles at, and how fast it gets there.
  Only when the level lies above the machine's own trip line in the OpenPLC interlock does the console show a
  **trip card**, with an estimate such as "press-1 trips in about 170 s". A machine that levels off
  below the line gets no card, and neither does a machine that is stopped or cooling. The fit must
  agree at three points ten seconds apart, so a guess made before the bend shows never flashes a card.
- **Slow drifts.** When the loop water warms slowly (Scenario 2), the curve shows no bend for many
  minutes. The API then uses the rule of a plant engineer: the supply water rises r degrees per minute,
  the machine is d degrees under its trip, so it trips in about d / r minutes. The rise must be steady
  and at least 0.1 °C per minute, and the card shows only inside 25 minutes.
  On the recorded runs of 25 September the old straight line put a card on a machine that never tripped
  in 254 passes. The curve did it in 12, and its estimate for Scenario 1 was 167 s against a real 180 s,
  where the line said 79 s. The watch of 26 September found the last false cards: early in a slow climb
  the curve still looks straight, so the fitted level is a guess (100 to 179 °C). The engine now waits
  until the bend shows (a time constant under 600 s). Real cards come 10 to 20 s later, still more than
  a minute before the trip.
- **Out-of-memory kills.** On the edge plane, the engine projects a service's memory working set to its
  memory limit. A service that leaks gets a card such as "OOM in about 2 minutes", before the kernel
  kills it.

The forecaster ignores flat levels and plateaus, such as a database filling its cache, and it ignores
trends that would cross the limit beyond the 15-minute horizon. A leak is self-caused, so it forms no
link to other services. It is a warning about one service, not a causal claim.

On the box, Scenario 5's first trip cards came 30 to 40 seconds after the fault. furnace-1's card came
76 seconds before its trip, and press-1's came 102 seconds before.

A trip card on a machine that VISR can control also gives the operator a proposal: derate it, so it makes
less heat and stays below the line. In Scenario 5 that is press-1 and press-2 on `plc-stamping`, cnc-1 on
`plc-machining` (its feed override) and furnace-1 on `plc-furnace` (its power limit).

## 10. Integrity: when a report breaks the physics

Industrial protocols like Modbus and S7comm have no authentication. Anyone who can reach a controller
can write to it. Stuxnet went further and replayed normal-looking values to the operators while the
machines misbehaved. So VISR adds a second kind of check: not "what caused this?", but "does this report
agree with the physics?" (`api/integrity.py`).

- **Unsigned setpoint change (Scenario 4A).** A writable setpoint on a PLC changed, and nothing
  explains it: no write intent in the API, no signed ledger row, no PLC restart, and no task load. VISR
  raises an `unsigned_write` finding and names the network client it saw writing. It also refuses to
  propose any action through that controller until an operator restores the setpoint with a signed
  write.
- **Current balance (Scenario 4B).** On each rail, the currents that the controllers report must add up
  to the independent feeder meter. If a controller reports a current that the meter does not see, one
  channel is lying. VISR raises a `current_balance` finding and names the channel.

Our principle for this part is short: **a fault with no physical cause is a security event.** These
checks detect. They do not block the writer, and we say so. Blocking belongs to network zones and device
identity. The zones are built and limit who can reach a PLC at all (chapter 13). An identity for each
device is on our roadmap.

## 11. The narrator: a local language model that only speaks

VISR uses one language model, and it runs on the edge box: Google's Gemma 4 in its small E4B edge size,
served by Ollama with the help of the box's small graphics card. This is our Gen AI.

The narrator has one job: turn an incident into a few sentences a person can read in seconds. It never
decides anything. The verdict exists before it speaks. If the narrator is slow or down, the console shows
template sentences built from the same facts.

**The incident record.** The engine answers every ten seconds, and each answer stands alone. An operator
needs the story, so the API keeps one (`api/incident.py`). An incident opens at the first sign: a root
cause, a forecast card, an integrity finding, a tripped machine, or a blind SCADA view. It keeps apart
the machine that **started** it (the origin) and the machine that **drives** it now. In Scenario 2 the
compressor that never unloads starts it, and once the chiller has run at its limit for a while, the
lost cooling can drive it. Each change is a numbered phase with a reason made of measured numbers, for
example "chiller-1 is at its capacity limit, and the supply water is 38 C against its 28 C setpoint". A new driver must hold for 15 seconds, so a flicker between
two suspects does not rewrite the story. A machine that its protective trip stopped is a consequence,
never the driver, and a suspect whose own load is normal is ignored. The incident closes 30 seconds
after the last physical sign.

**Words locked to the story.** The narrator writes from a case file of these facts, in a fixed order:
what happened, what started it, what drives it now, the chain, what the operator did, the evidence, the
forecast, and what to do. Readings from the start of the incident go to the past tense once they stop
being true: after press-1 trips, the text says "at the start, press-1 drew 85 A", not "draws". It writes a new text only when the incident enters a new phase, so the words stay still while the
live numbers move in their own places. A checker refuses any sentence with a number that is not in the
case file, and the template sentence takes its place. On the box, Gemma 4 wrote each phase in 8 to 15
seconds in the background, and every section passed the checker.

**Ask VISR.** An operator can type a question under the verdict, such as "why is chiller-1 driving it
now?". The model answers with three read-only tools: the incident, one machine's readings, and the
checked suggestions. On the box it picked the right tools and answered in 5 to 11 seconds. It cannot
write anything, and an answer with a number that no tool returned is replaced by a pointer to the panel.

We made this split on purpose. A language model is good at explaining and bad at being sure. The engine
is the opposite. Each does the job it is good at.

## 12. Acting safely: the act loop

Most monitoring tools stop at the alert. VISR goes one step further, carefully. The act loop is written
up in `FLEET.md` section 10. Its rules are strict:

1. **Bounded verbs.** VISR can execute three things: derate a machine through its PLC to a fixed
   percentage, stop compressor-1 through its PLC, and restore a setpoint that someone changed without a
   signed record. It proposes a derate for the root machine while that machine draws more than its
   normal current, and for any machine it can control that has a trip card open. When compressor-1 is
   the root, the proposal is a stop, because its setpoint is its run command. VISR never proposes an
   action on chiller-1: less cooling only makes the loop hotter. Every other suggestion (stop a stuck load, isolate a noisy
   network client, restart a leaking service, reset a relay only after its cause is fixed) is advisory:
   the console shows it, and a person does it. No suggestion acts on a tripped machine. There is no
   free-form command.
2. **Cite or die.** Every proposal cites the verdict it acts on: the root, its evidence, and its
   confidence. If the verdict changes before the operator confirms, the API refuses the action with a
   409, and the refusal goes into the ledger.
3. **A human confirms.** The operator reads the confirmation and confirms. Nothing runs by itself.
4. **One write, through the real protocol.** The API asks the tag server to write one setpoint to the
   PLC over S7comm or Modbus. It never touches the plant model directly, and it never touches the
   safety interlock.
5. **Measure the relief.** About a minute after the write, the API measures the machine's current and
   the rail voltage again, and records the before and after values in the ledger.
6. **Refuse through a controller it cannot trust.** If an integrity finding is open on that PLC, the
   proposal is blocked.
7. **Restore is audited too.** Returning the setpoint to normal is a signed, recorded action.

## 13. Security and trust

VISR is built for a secure industrial theme, so we mapped it to the seven foundational requirements
(FR1 to FR7) of ISA/IEC 62443-3-3, the international standard for industrial control system security.
We are aligned with 62443 thinking. We are not certified, and we say so.

| Requirement | Built today | Next on our roadmap |
|---|---|---|
| FR1 Identification and authentication | TLS login, an operator token, per-PLC signed enrollment tokens, no vendor default passwords | an account for each person |
| FR2 Use control | viewer and operator roles, a writable-tag list with limits | per-device keys, then X.509 certificates |
| FR3 System integrity | a hash-chained audit ledger, integrity checks against the physics | check each PLC task hash against the approved task |
| FR4 Data confidentiality | TLS at the console, inference on one air-gapped box, Secrets mounted as files, no plant traffic to the internet | Modbus/TCP Security (TLS) where PLCs support it |
| FR5 Restricted data flow | the safety interlock kept off the control network segment, network zones with deny-by-default policies, no side doors around the login | zones for the monitoring namespace |
| FR6 Timely response to events | every refused command writes a ledger row | a durable ledger with an anchored chain head |
| FR7 Resource availability | edge-first, no cloud, forecasts for its own memory, a memory and a disk limit on every pod, 30 days of history on the slow disk | image scanning and a software bill of materials |

A few details, in plain words:

- **The front door.** The console sits behind TLS and a login with two roles. A viewer can look. An
  operator can act. The web server adds the operator's token to requests only for the operator role, and
  the API checks that token on every state-changing call. An anonymous request gets a 401.
- **The ledger.** Every action and every refused attempt goes into an append-only log in which each row
  carries the hash of the row before it (`api/security.py`). If anyone edits or deletes a row, the chain
  breaks at that point, and the console's chain lamp shows it.
- **Device identity.** Each virtual PLC gets its own token, derived from a secret enrollment key and the
  PLC's name. SCADA accepts an enrollment only with the right token.
- **No passwords in the code.** Every password lives in a Kubernetes Secret, generated at random on the
  box. A secret scanner once found a demo database password in one of our deployment files. We moved it
  into a Secret and changed it in the running database, so the old value opens nothing (LOG-076). The
  OpenPLC web interface no longer accepts its vendor default login, and its REST API is switched off
  (LOG-077).
- **Zones and conduits.** Each namespace is a zone: the plant floor, the PLC fleet, and VISR itself.
  A deny-by-default network policy lets through only the connections that the eBPF map saw in real
  use: the plant model to the PLCs, SCADA to the PLCs and the historian, VISR to what it reads. A pod
  that has no business with a PLC cannot reach it. The one exception is the test attacker of Scenario 4A,
  which is labeled as such, runs only during that scenario, and does not exist in a real plant.
  `deploy/netpol.sh` applies the policies, checks every path, and removes them again if one fails.
- **Nothing leaves the box.** The eBPF map caught the historian database sending usage telemetry to the
  internet. We switched it off, and the plant and PLC zones can no longer reach the internet at all.
- **Least privilege in every pod.** No VISR service runs as root except three that need it (the web
  server, the OpenPLC runtime, and the database, which drops to its own user). The others run as an
  unprivileged user, with no Linux capabilities, a read-only file system, and no Kubernetes token. Secrets
  arrive as files, not as environment variables. The API, Prometheus, and Grafana have no side doors on
  the node: everything goes through the console's TLS login. The OpenPLC web page keeps its node port
  by our choice, behind its own random password.
- **Refusals are part of the demo.** `deploy/refusals.sh` shows three refusals on camera: a command
  with no token (401), an action on a stale verdict (409), and an attempt to delete the base PLC (403).
  Each one writes a ledger row.

## 14. The console

The console (`dashboard/`) is one screen. The operator never scrolls and never zooms. We designed it
after the ISA-101 idea of a high-performance HMI: mostly calm grey, with no explanation text. Only the
status markers take a colour.

- **Teal** means healthy.
- **Amber** means a warning, a forecast, or an injected fault.
- **Red** means an alarm or a broken rule.
- **Blue** is kept for commands, the buttons that change the plant.

The layout, left to right:

- **Left column:** *Assets*, every machine grouped by rail and coolant loop with live volts, amps and
  temperatures, and the *Event log*, which is the audit ledger itself.
- **Centre:** the *Map*, a 3D plant floor drawn with three.js, where the causal chain lights up along
  the real wiring and piping. It switches between the plant FLOOR and the EDGE computer. Below it are
  detail tabs: *Selected* (one machine's values, its Grafana trend and its SCADA tags), *Fleet* (the PLCs and Add PLC), *Tags* (the SCADA tag
  browser), *Trends* (Grafana graphs) and *Edge* (the computer's own health).
- **Right column:** the *Verdict* and the *Actions*. The Verdict follows the incident record (chapter
  11): STEADY, FORECAST, ROOT CAUSE, INCIDENT (an integrity finding or a blind SCADA view with no
  driver), or RECOVERING. It shows the current driver, the machine that started the incident, the
  reason, the evidence chips, the chain, the narrator text, the last phases, and the Ask box. The
  Actions panel holds the act loop and the advisory notes.

The operator can drag the gaps between panels to give any panel more room.

Above everything sits a command bar with a lamp for each subsystem: engine, aggregator, SCADA, PLC
link, historian, fleet, authentication, the audit chain, and integrity. Only an abnormal lamp shows by
itself, and the healthy ones fold behind one pill. Three buttons set the text size.

The map uses a perspective camera, so a far machine looks smaller than a near one. Energy and coolant
flows move along the bus bars and the coolant trench at their measured speeds, and a value plate stays
on every machine or rail in warning or alarm.

When the console opens, a short **boot screen** runs six real probes (engine, telemetry, workloads,
causal graph, plant, PLC fleet) and shows each measured round trip. Our rule for the whole interface
comes from that screen: **nothing on this console animates unless it actually happened.**

The visual style borrows lightly from the visor of the Halo games: a single accent colour, thin corner
brackets, and small uppercase labels. Our design rule was "clarity is the product, Halo is the accent",
about ninety parts clarity to ten parts flavour. The colours follow the Stage 2 palette of the
competition.

---

# Part IV · Proof

## 15. The seven scenarios

A root-cause tool is only as good as the faults it has been tested on. We built seven, one for each way a
plant fails between machines, and anchored each one on a real incident. The contract for all of them is
`SCENARIOS.md`. The console and this book call them Scenario 1 to 6. The code and the scripts call them
PS1 to PS6 (plant scenarios).

| Scenario | What fails | Real incident behind it | What VISR must show | Measured on the box, 22 Sep 2026 |
|---|---|---|---|---|
| 0 | Nothing: the steady plant | none | silence: no root, no finding | QUIET every minute after a 15-minute settle |
| 1 | Rail-sag cascade | Milford Haven, 1994 | root press-1 along rail A | 80.3 s, write + rail + temporal |
| 2 | A failed pressure sensor overheats the loop | Azure Australia East, 2023 | root compressor-1, then its heat reaches the coolant loop | measured on 26 Sep: root 21 s, loop link 189 s (the old Scenario 2 on 22 Sep: 304.2 s) |
| 3 | Control network storm | Browns Ferry Unit 3, 2006 | root hmi-gw along the segment | 90.2 s |
| 4A | Setpoint write with no record | Stuxnet 2010, FrostyGoop 2024, Ukraine grid 2015 | an unsigned-write finding, client named | 39.1 s, client rogue-ews |
| 4B | A current report contradicts the feeder | Stuxnet's replay, Buncefield 2005 | a current-balance finding, channel named | 15.0 s, an 18.1 A gap |
| 5 | Coolant pump degradation | LG Polymers, 2020 | root chiller-1 and trip cards before the trips | cards 76 s and 102 s before the trips |
| 6 | The monitor runs out of memory | Toyota 2023, Northeast blackout 2003 | a leak forecast, then an honest "blind" view | card at 90.4 s, blind at 205.1 s |

A few words on each.

**Scenario 1, the rail-sag cascade.** Chapter 3 told this one in full. It is the alarm flood of Milford
Haven in miniature: one fault, many alarms, one real cause.

**Scenario 2, a failed pressure sensor overheats the loop.** In 2023, a power disturbance tripped the
chillers of a Microsoft Azure data centre in Australia. The chilled water then ran too warm for them to
restart, and the cooling that was left could not carry the heat load. In our plant, the pressure sensor
of compressor-1 fails and reads low, so its controller never unloads it. The air receiver vents at its
safety valve, and the water-cooled compressor puts more heat into the loop than chiller-1 can remove.
The loop water warms by about a degree a minute, every cooled machine warms with it, and furnace-1
trips first, about 16 minutes later. VISR must name compressor-1 and follow its heat into the loop.
The first version of this scenario tripped the chiller's relay under a rail sag. It passed on the box on
22 September, but a review found that the relay tripped at 1.04 times its rating. The standard says a
relay must not trip at 1.05 times its setting. We replaced it with a fault that a plant engineer would
recognize.

**Scenario 3, a control network storm.** At Browns Ferry in 2006, excess traffic on a plant network
froze the controllers of two recirculation pumps, and the reactor was shut down by hand. In our plant,
the HMI gateway floods the stamping cell's network segment. The PLC link lags and drops. VISR names the
gateway along the segment, not the victim PLC.

**Scenario 4A, a setpoint write with no record.** Stuxnet in 2010, the Ukraine grid attack in 2015 and
FrostyGoop in 2024 all wrote to industrial controllers that had no authentication. In our plant, a rogue
engineering workstation writes one setpoint on the stamping PLC over S7comm, outside SCADA. VISR notices
the unexplained change and names the client.

**Scenario 4B, a report that contradicts the feeder.** Stuxnet replayed normal values to the operators.
At Buncefield in 2005, a stuck level gauge let a tank overfill. In our plant, press-1 really draws more
current, while its PLC channel replays the last 30 seconds of normal values. The feeder meter sees the
truth. VISR finds the gap and names the lying channel.

**Scenario 5, coolant pump degradation.** At LG Polymers, the tank heated with no sensor where it
mattered. In our plant, the pump weakens and every cooled machine warms, each at its own pace. VISR names
chiller-1, the pump side of the loop, as the root, and forecasts each trip before OpenPLC trips the
machine.

**Scenario 6, the monitor runs out of memory.** At Toyota in 2023 a full disk stopped the plants, and in
the 2003 Northeast blackout the alarm system itself failed silently. In our system, the SCADA tag server
starts to leak memory. VISR forecasts the out-of-memory kill before it happens. After the kernel kills
the process, the console says plainly that the SCADA view is blind, instead of showing old numbers as if
they were live.

**The refusals.** Not a fault, but part of every run: a command with no token, a command on a stale
verdict, and a delete of the base PLC are all refused, and all recorded.

**Scenario 7, a supply dip (built, not yet passing).** A dip on the incoming supply sags every rail at
once. VISR must name the supply, not a machine. The physics and the common-mode rule are built and
unit-tested, but in our offline replay a constant-power machine's reaction to the dip still looks like
an aggressor, and the engine names that machine. The fix is known and planned. Until it passes, Scenario
7 stays out of the demo and out of the deck.

## 16. How we test and prove it

We follow one rule: **every phase ends proved on the real box, from the runbook, not on a laptop
preview.** The proof comes in layers.

1. **Unit tests.** 395 tests across the five Python suites (engine 89, plant 52, API 102, SCADA 103,
   virtual PLC 49), 3 Go tests for the aggregator, plus a production build of the console. The engine's core pass is a pure function, so its tests
   use fixed synthetic signals and never flake.
2. **The offline replay** (`correlation/tests/replay_offline.py`). It drives the real plant model,
   samples it on the engine's own five-second grid, and runs the real engine pass. It catches a rule
   that is right on its own and wrong once the rest of the plant answers. It prints one line per
   scenario: expected root, measured root, PASS or FAIL.
3. **The Scenario 2 lab** (`correlation/tests/ps2_lab.py`). A focused harness that runs the real engine
   memory and merge, emulates the OpenPLC latch at each machine's own trip, and measures when the root and
   the loop link of Scenario 2 hold together. It measures relative improvement. Only the box can give the
   verdict. Its sister, the idle lab (`idle_lab.py`), counts false roots in a calm plant after a fault.
4. **Go-live checks** (`deploy/golive.sh`). After every deploy, 53 checks (26 September) from outside
   and inside the cluster: the login wall, the 401s, every rollout, the plant, both PLC protocols, the tag quality, the
   historian, the engine, and the API views.
5. **The factory reset** (`deploy/factory-up.sh`). It removes rehearsal PLCs, pushes the images, runs
   the go-live checks, backs up and wipes the engine memory, and starts the soak watcher.
6. **The soak.** The plant runs with no faults while a watcher writes one verdict line per minute. The
   soak passes when the last 30 lines are all QUIET: no finding, no root, no forecast, no integrity
   finding. On 22 September the plant went quiet 15 minutes after the restarts and stayed quiet.
7. **The proof run** (`deploy/proof-run.sh`). About 45 minutes of timed checks on the box: it fires each
   scenario, times the evidence, confirms the act loop and the relief, resets, times the clear, and
   saves every answer the API gave as evidence. Then `deploy/refusals.sh` runs the refusals.

On 22 September 2026, the third proof run passed **all seven scenarios and every refusal** for the first
time. Appendix C has the numbers.

## 17. What went wrong, and what it taught us

Every one of these problems was found by a test on the real box. We list them because they made the
system better, and because they are the honest story of how it was built.

- **The engine learned zeros.** Our first 24-hour soak was quiet in only 1 minute of 1,438. The engine
  had restarted before its window filled, and it stored every plant baseline as zero. Every normal value
  then looked like a fault. The fix: a baseline learns only from a window that is at least 90 % full.
  *Lesson: test the restart path, not only the steady state.*
- **A fault that showed nothing.** Our first Scenario 2 sag looked exactly like a normal compressor
  cycle, so the engine rightly ignored it. We added a motor overload relay to the chiller that trips only
  on a sustained sag, as in the 2023 chiller trip. *Lesson: prove that a fault is visible before you
  demo it.*
- **The trip beat the verdict.** The engine needs about 80 seconds to confirm a root. In our first proof
  run, hot machines tripped in 40 to 95 seconds, so the verdict arrived after the damage. We slowed the
  thermal time constants to between 1.5 and 4.5 minutes, which is closer to real equipment. *Lesson:
  test the whole loop on the box, with the real interlock.*
- **The latch ate the rail hop.** In Scenario 2, three machines reached the 78 °C latch and tripped.
  That unloaded rail B, the sag vanished, and the first hop of the chain disappeared before the second
  one formed. We measured seven settings of the chiller's residual flow in the lab, picked the start of
  the plateau, and the box then passed. *Lesson: when two effects must coexist, measure the window where
  they do.*
- **A relay that no standard allows.** The chiller relay of the first Scenario 2 tripped at 1.04 times
  its rating, and IEC 60947-4-1 says a relay must not trip at 1.05 times. The rails also sagged 10 % in normal
  running. The scenario passed, but an engineer would not believe it. We checked every plant value
  against a published source and rebuilt Scenario 2. *Lesson: a demo that passes on a wrong model
  proves nothing. Cite the source of each number.*
- **A missing line in a manifest.** The first deploy of the realism pass left out one `---` line
  between two objects in the fleet manifest. Kubernetes merged them into one, with no error, and two
  PLC services never existed. The go-live checks caught it, and the script now counts the objects in
  each manifest before it applies anything. *Lesson: check what the cluster received, not what the
  command printed.*
- **A memory signal counted twice.** Two collectors scraped the same memory counters, so the forecast
  signal doubled (63 MiB against a real 31 MiB). The fix pins the query to one collector. *Lesson: check
  a number against a second source before you forecast with it.*
- **Trips that did not latch.** Our first OpenPLC program upload failed quietly, so trips reset
  themselves. We fixed the upload flow and now check at every start that the program runs. *Lesson: a
  safety function needs a check that it is actually running.*
- **A repeated click did damage.** The fault controls could send the same fault twice. A second Scenario 4B
  recorded its replay from the already faulted current, and the evidence vanished. Faults are now
  idempotent: a repeat changes nothing. *Lesson: every operator action must be safe to repeat.*
- **A password in a file.** A secret scanner flagged a demo database password in a public deployment
  file. We moved every password into cluster Secrets, generated them at random, and changed the live
  one. *Lesson: a demo password is still a password.*

---

# Part V · Where the ideas come from

## 18. Our inspirations

VISR stands on many shoulders. This chapter names them.

### Real incidents (the anchor of every scenario)

- **Milford Haven refinery, 1994** (UK HSE case study): the alarm flood, 275 alarms in 11 minutes.
- **Texas City refinery, 2005** (US CSB report): how a process upset spreads when instruments mislead.
- **Buncefield, 2005**: a stuck level gauge and an overfilled tank.
- **Browns Ferry Unit 3, 2006** (US NRC notice): network traffic that froze two pump controllers.
- **Stuxnet, 2010** (Ralph Langner, *To Kill a Centrifuge*, 2013): unauthenticated writes plus replayed
  normal values.
- **Ukraine grid, 2015** and **FrostyGoop, 2024** (Dragos): attacks that wrote to industrial controllers.
- **LG Polymers, Visakhapatnam, 2020**: cooling that stopped, and a sensor that was not there.
- **Toyota, 2023**: a full disk that stopped 12 plants. **Northeast blackout, 2003**: an alarm system
  that failed silently. Both taught us to watch the watcher.
- **Azure Australia East, 2023** (Microsoft incident report): a power disturbance, a chiller trip, chilled
  water too warm for a restart, and cooling capacity too small for the heat load.
- **Jaguar Land Rover, 2025** (Cyber Monitoring Centre): the cost of a cyberattack on manufacturing.

### Standards

- **ISA/IEC 62443**: the security requirements FR1 to FR7 that shape chapter 13.
- **ANSI/ISA-18.2 and EEMUA 191**: alarm management, and the numbers behind "alarm flood".
- **ISA-101**: the high-performance HMI ideas behind the console's calm grey and meaningful colour.
- **IEC 61131-3**: the PLC programming standard. Our trip program and our virtual PLC tasks are
  Structured Text.
- **Modbus TCP and S7comm**: the real protocols our PLCs and SCADA speak. **Modbus/TCP Security** is on
  our roadmap.
- **OPC UA and MQTT Sparkplug**: the common languages of industrial data, and our target for future
  adapters.
- For a future vehicle rung: **VSS** (the COVESA vehicle signal naming tree), **KUKSA** (an open signal
  broker) and **SOVD, ISO 17978** (remote vehicle diagnostics over HTTP and JSON). SOVD standardizes the
  access layer. Nobody has standardized the reasoning on top, and that is the seat VISR aims for.

### Research

- **Bauer, Cox, Caveness, Downs and Thornhill, 2007** (IEEE Transactions on Control Systems Technology):
  transfer entropy finds the direction in which a disturbance travels through a process. It showed us
  that "what moved first" is a real and useful question.
- **Schleburg, Christiansen, Thornhill and Fay, 2013** (Journal of Process Control): a plant connectivity
  model groups alarms by cause and cuts alerts by 70 to 80 %. It is the closest ancestor of our
  shared-medium gate.
- **Wu, Tordsson, Elmroth and Kao, MicroRCA, 2020** (IEEE/IFIP NOMS): graph-based root cause for
  microservices without code changes. It inspired the edge plane.
- **Giraldo and colleagues, 2018** (ACM Computing Surveys): a survey of physics-based attack detection in
  cyber-physical systems. It is the ancestor of our integrity checks.
- **Classic statistics**: the EWMA and CUSUM changepoint tests from quality control, lagged
  cross-correlation, and outlier-resistant estimates with the median absolute deviation.

### Products that set the bar

- **Dynatrace Davis AI**: automatic root cause for software systems. It showed us how good causal
  answers can feel, and it works on software, not plant machines.
- **Siemens Senseye with the Industrial Copilot for maintenance**, and **GE Vernova SmartSignal**: strong
  per-asset failure prediction from long histories. VISR sits beside tools like these. It does not
  replace them.
- **Edge runtimes such as AWS IoT Greengrass and Azure IoT Edge**: the pattern of a local runtime at each
  site that works alone, plus a central view that federates many sites. Our one-box stack is that local
  runtime.

### Open-source building blocks

k3s (small Kubernetes), Prometheus, Grafana, Loki and Alloy (metrics, dashboards and logs), node-exporter
and the Linux kernel's pressure stall information (PSI), Caretta (an eBPF service map), OpenPLC v3 (the
PLC runtime), TimescaleDB (the historian), pymodbus and python-snap7 (Modbus and S7comm), FastAPI (the
API), Go (the aggregator), SQLite (the engine memory), Next.js, React and three.js (the console and its
3D floor), nginx (TLS and the login), Ollama and Google's Gemma (the narrator), and a local Docker
registry.

### Design

- **The visor of the Halo games** gave us the name and a light accent: one accent colour, thin
  brackets, small labels. Clarity came first.
- **The InnoVent Stage 2 template** gave us the palette and the fonts of the deck and the console.

## 19. How VISR compares

This comparison uses public product pages and papers, as of September 2026. It is the same assessment
as the benchmark slide of our deck, which lists its sources.

| Capability | VISR | Siemens Senseye | GE Vernova SmartSignal | Dynatrace Davis AI | Topology alarm grouping (2013 paper) |
|---|---|---|---|---|---|
| Root cause across plant machines | yes | no | no | partly: software services, not plant machines | partly: groups alarms by cause from a plant model |
| A causal link needs a physical shared medium | yes | no | no | no | yes |
| The plant and the edge computer in one graph | yes | no | no | not found | no |
| A human-confirmed PLC action that cites the verdict | yes | no | no | no | no |
| Runs air-gapped on one edge box | yes | no: a cloud service on Microsoft Azure | not found | not found | not found |
| Per-asset failure prediction from long history | partly: trip ETAs from live signals, not long-horizon models | yes | yes | no | no |

"Yes" means offered, "partly" means partly offered, "no" means not offered, and "not found" means we did
not find it in public documentation, which is not proof that it does not exist. The honest summary:
VISR is strong at the cross-machine cause, the physical evidence, and the safe action, on one box. The
established tools are strong at long-horizon prediction for single assets. They are complementary.

What is new in VISR is not one algorithm. Correlation, changepoints and plant topology are all known,
and we say so. What is new is the combination:

1. a shared-medium gate, so a correlation becomes a causal link only along a declared rail, loop,
   segment or computer,
2. two planes in one graph: the plant and the computer that watches it,
3. an act loop that goes through the real PLC protocol, with a human in the loop and a citation on every
   action,
4. integrity checks that turn a physically impossible report into a security finding,
5. all of it on one air-gapped box, from protocol frames to the verdict.

---

# Part VI · Where we are going

## 20. What we intend to achieve

### The near term

| When | What |
|---|---|
| July 2026 | Stage 1: the first prototype |
| 30 September 2026 (done) | Stage 2: the deck and the demo video, recorded on the box |
| 2 October 2026 (done) | Stage 2: the pre-read for the jury |
| October 2026 | the virtual PoC presentation |
| October to November 2026 | the hardware rung: a real PLC and a power analyzer in the same graph |
| November to December 2026 | the security roadmap: an account per person, per-device keys, a durable ledger |
| Mid-December 2026 | the prototype, frozen and soaked |
| January 2027 | the Stage 3 final demo |

### The roadmap, in plain words

- **Causes above the plant.** Scenario 7 must pass: when the supply dips, VISR must name the supply, not
  the machine that reacted to it. The fix is an "explained load" test: a machine whose extra current is
  fully explained by the dip it suffers must not be called the aggressor.
- **Slow chains.** Since the realism pass, Scenario 2 builds over about 16 minutes, as a real heat
  problem does. The root comes at once, but the link to the loop took about three minutes on the box
  (189 s on 26 September). We want the loop link sooner, from the rise of the supply water itself.
- **Real hardware in the same graph.** A physical PLC and a power analyzer, the equipment that a real
  plant uses, join the same engine through a thin adapter. Each new kind of device should cost an adapter,
  not a redesign.
- **More act-loop verbs.** More bounded, reversible actions, each still cited, confirmed and measured.
- **Security.** Network zones for the monitoring namespace and on the real plant network, an account per
  person, per-device keys and then X.509 certificates, task-hash checks on every PLC, an anchored ledger that no one on the box can quietly
  rewrite, and image scanning with a software bill of materials.

### Where cloud services fit

VISR's verdict path stays on the edge box and must work offline. Cloud services, including the AWS
services our sponsors provide, fit around it:

- **Now:** a standby copy of the edge box on a cloud virtual machine for the PoC, storage for the
  evidence bundles and the demo video, and a budget alarm on the credits.
- **For the prototype:** a write-once storage bucket with a signing key to anchor the audit chain, a
  container registry with vulnerability scanning and a bill of materials for every image, and a device
  certificate service for the field devices.
- **Optional, later:** a cloud language model for longer incident reports and shift summaries (never in
  the verdict path), an industrial data platform to publish verdicts into, and remote management for
  many edge boxes.

### The north star

Everything above adds up to one sentence we are building toward:

> **Any industrial asset (a service, a microcontroller, a PLC, and one day a vehicle) can join one secure
> plane, where an authorized engineer can see its live health, get a causal verdict with evidence, and
> take one approved action, without caring what protocol the asset speaks underneath.**

Industry has already standardized how to reach assets: OPC UA and MQTT in factories, SOVD for vehicles.
What nobody has standardized is the reasoning on top: what caused this, how sure are we, what happens
next, and what is safe to do. That is the gap VISR fills.

### What success looks like

For the final demo, we want a prototype that a plant engineer could run on one box and trust:

- it stays **silent** when the plant is calm, for a whole day,
- it names the **cause** of a cross-machine fault with physical evidence, in about a minute and a half,
- it **warns** before trips and crashes,
- it catches a controller that **lies** or a setpoint that changed with no record,
- it lets a human apply one **safe** fix and **measures** the effect,
- and it records **everything** in a ledger that shows any tampering.

For a real plant, that means fewer alarms per incident, a faster path from symptom to cause, cascades
stopped before the damage, and an operator who can see why the system believes what it says.

## 21. Our honesty rules

We wrote these rules early, and we still follow them.

1. **Measured or labeled.** Every number on a screen or a slide is measured on the box, with a place and
   a date, or it is visibly marked as a simulation or a forecast.
2. **The plant is a model, and it says so.** The plant is physics-simulated. The edge computer is real.
   The inference on top of both is real.
3. **The engine is statistics.** We do not call it a neural network or machine learning. It is
   deterministic statistical inference behind a physical gate. The language model only narrates.
4. **Gates never loosen for a demo.** If a demo needs a looser rule, the demo is wrong.
5. **Aligned, not certified.** We follow IEC 62443 thinking. We do not claim certification.
6. **Detect, not block.** The integrity checks find a rogue writer. They do not stop it.
7. **Roadmap is spoken as roadmap.** Anything not built yet is presented as a plan, never demoed as if
   it were real.
8. **A virtual PLC is a profile, not firmware.** It speaks real protocol frames, and it is our own
   runtime.
9. **Failures stay visible.** A scenario that does not pass says so in the contract, in the log and in
   this book, until it passes on the box.

---

# Appendices

## A. Glossary

- **Act loop:** VISR's way of acting: one bounded verb, cited, confirmed by a human, written through the
  PLC, and measured afterwards.
- **Aggregator:** the service that builds one five-second window of every signal for the engine.
- **Baseline:** the learned normal band of a signal.
- **Blast radius:** the set of machines a root cause affects.
- **Case:** a stable incident that the engine remembers beyond the telemetry window.
- **Changepoint:** the moment a signal's behaviour changed.
- **Common mode:** a disturbance that moves every member of a medium together, with none leading.
- **CUSUM:** cumulative sum, a classic test for a sustained shift in a signal.
- **Edge box:** the single computer at the plant that runs all of VISR.
- **Feeder meter:** the true total current on a rail, independent of the controllers.
- **Field port:** the connection through which a PLC commands its machines, and the plant reports back.
- **Forecast card:** a warning that a limit will be crossed, with a time estimate.
- **Historian:** the time-series database of plant readings.
- **Integrity finding:** a report that breaks the physics or a change with no record.
- **Interlock:** a safety function that trips a machine when a limit is crossed. Ours is OpenPLC.
- **Ledger:** the hash-chained audit log of every action and refusal.
- **Medium, shared medium:** a physical thing two machines share: a rail, a loop, a segment, a PLC cell,
  a computer.
- **Narrator:** the local language model that explains a verdict in words.
- **PLC:** programmable logic controller, the computer that runs a machine.
- **Proof run:** the timed, evidence-saving run of every scenario on the box.
- **PSI:** pressure stall information, the Linux kernel's measure of how long work waited for CPU,
  memory or disk.
- **Rail:** a power supply line shared by several machines.
- **Root cause:** the one node that best explains the observed trouble.
- **S7comm, Modbus TCP:** industrial protocols that PLCs speak.
- **SCADA:** the supervisory system that reads PLCs and shows the plant. Ours is the tag server.
- **Scenario:** one of our fault families. Scenarios 1 to 6 (with 4A and 4B) make seven, and scenario 0
  is the steady plant. The code calls them PS1 to PS6. Scenario 7 is still in progress.
- **Soak:** hours of running with no faults, to prove the system stays silent.
- **Structured Text:** the IEC 61131-3 text language for PLC programs.
- **Tag:** one named, quality-rated value in SCADA.
- **Trip:** a safety stop of a machine.
- **Verdict:** the console's answer: STEADY, FORECAST, ROOT CAUSE with evidence, INCIDENT, or RECOVERING.
- **Virtual PLC:** our own soft PLC runtime with a real protocol profile.
- **Witness:** the evidence that two nodes share a medium.

## B. Where things live in the repo

| Path | What it holds |
|---|---|
| `plant/` | the physics plant model and its tests |
| `plc/` | the OpenPLC trip program, its entrypoint and its register map |
| `vplc/` | the virtual PLC runtime: the Structured Text compiler, the protocol servers, the tasks |
| `scada/` | the SCADA tag server, its protocol drivers, and the historian writer |
| `aggregator/` | the five-second window (Go) and its query pack |
| `correlation/` | the causal engine: detectors, correlation, gate, ranking, memory, merge, forecasts |
| `api/` | the API: login gate, audit ledger, act loop, integrity checks, fleet routes |
| `dashboard/` | the operator console |
| `deploy/` | manifests and the scripts: go-live, factory-up, proof run, refusals, Secrets, network zones, the fault shell |
| `soak/` | the soak recorder and its evidence report |
| `video/` | the window placement and capture scripts of the demo takes |
| `docs/images/` | the architecture diagram of the README |
| `SCENARIOS.md` | the contract of the scenario set |
| `FLEET.md` | the contract of the virtual PLC fleet and the act loop |
| `PIVOT_SETUP.md` | the runbook to bring the box up |
| `POC_SCRIPT.md` | the recording script of the demo |
| `INNOVENT_PLAN.md` | the current state and the open items |
| `INNOVENT_MASTER_PLAN.md` | the stage plan of July 2026 and the standing rules |
| `INNOVENT_LOG.md` | the append-only decision log, the authoritative history |
| `BOOK.md` | this book |

## C. The numbers we measured

Third proof run, edge box, 22 September 2026, 16:26 to 17:10 IST. All seven scenarios and every refusal
passed.

| Check | Result |
|---|---|
| Soak before the run | 123 QUIET minutes of 138, QUIET since 14:23:59 |
| Command with no token | refused, 401 |
| Action on a stale verdict | refused, 409 |
| Delete of the base PLC | refused, 403 |
| Scenario 1 root press-1 | 80.3 s, evidence write + rail + temporal on rail voltage |
| Scenario 1 first trip card | 30.2 s, and press-1 never tripped |
| Execute relief, press-1 current | 85.3 → 45.0 A |
| Execute relief, rail A voltage | 344.4 → 359.6 V |
| Scenario 1 clear after the reset | 305.7 s |
| Scenario 5 first trip | furnace-1 at 116.5 s, its card 76.3 s earlier (press-1's card 102.5 s earlier) |
| Scenario 2 root compressor-1 with the loop hop (the old Scenario 2, before the realism pass) | 304.2 s |
| Scenario 3 root hmi-gw | 90.2 s |
| Scenario 4A unsigned write | 39.1 s, client rogue-ews named |
| Scenario 4B current balance | 15.0 s, a gap of 18.1 A |
| Scenario 6 leak card, then blind | 90.4 s, then 205.1 s, SCADA back 3.0 s later |
| Audit chain | intact, 29 rows |
| Go-live checks before the soak | 42 passed, 0 failed |
| Add PLC to the engine window | 15.6 s, six measured phases (September 2026) |

The raw evidence of every run is kept on the box. The history of every decision behind these numbers
is in `INNOVENT_LOG.md`.
