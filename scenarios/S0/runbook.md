# S0 - false-positive drill (the credibility scenario)
Trigger: nothing. 10 minutes of steady factory.
Pass: zero causal edges, zero root-cause cards, no `incipient` forecast, firmware-cache shows high RAM
with no alert ("usage is not pressure"). The AI insight feed reads "no active findings — steady state"
and the verdict reads "Steady state…". Run x3 before any demo.
Idle-write guarantee: cooling-monitor's steady journal is a bare-minimum **unsynced** heartbeat (a few
lines every ~10s, truncating) so steady state writes ~nothing to the shared disk and the engine can't
blame it as a source (LOG-086). If a verdict appears at idle, suspect (a) an idle writer regressed
[check cooling-monitor JOURNAL_* / dcim-bridge], or (b) a gate-threshold tuning regressed -- check the
last BUILD_LOG tuning entry and revert it.
