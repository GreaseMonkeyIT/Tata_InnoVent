(* utilities: the plant utilities PLC (LOG-100). compressor-1 is machine k = 0 and chiller-1 is
   k = 1, both on rail psu-b (FLEET.md 3.1). Instruments from the field port: %IW32 the compressor
   pressure transducer (bar x100), %IW33 the loop supply temperature (C x10), %IW34 the loop flow
   (L/min x10), %IW35 the chiller load (% x10).
   compressor-1: a load/unload band on the transducer reading, as a compressor controller runs it
   (Atlas Copco recommends at least 0.6 bar between load and unload). It loads at or below LOAD_P
   and unloads at or above UNLOAD_P. SPEED_PCT is the load: 100 loaded, 25 unloaded. Its
   DERATE_PCT (%MW10) is the run command: 0 stops it, 1..100 runs it. After a stop it waits
   MIN_STOP before it may run again (the controller's minimum stop time).
   chiller-1: runs while the cell is enabled, it is READY (its overload relay is closed), and its
   DERATE_PCT (%MW11) is above 0. SPEED_PCT is that value: the demand limit in % of capacity.
   The chiller's own unit controller keeps its anti-recycle timer. *)
PROGRAM utilities
  VAR
    comp_ready     AT %IX0.0 : BOOL;
    chiller_ready  AT %IX0.1 : BOOL;
    air_p          AT %IW32  : INT;   (* bar x100, the transducer reading *)
    supply_t       AT %IW33  : INT;   (* C x10 *)
    loop_flow      AT %IW34  : INT;   (* L/min x10 *)
    chiller_load   AT %IW35  : INT;   (* % x10 *)
    comp_run       AT %QX0.0 : BOOL;
    chiller_run    AT %QX0.1 : BOOL;
    comp_speed     AT %QW0   : INT;
    chiller_speed  AT %QW1   : INT;
    cell_enable    AT %MW8   : INT := 1;
    comp_cmd       AT %MW10  : INT := 100;
    chiller_limit  AT %MW11  : INT := 100;
    load_p         AT %MW21  : INT := 690;   (* bar x100 *)
    unload_p       AT %MW22  : INT := 750;   (* bar x100 *)
    loaded_word    AT %MW23  : INT;          (* 1 while the compressor is loaded *)
  END_VAR
  VAR CONSTANT
    MIN_STOP : TIME := T#120s;
    UNLOADED : INT := 25;
  END_VAR
  VAR
    enabled     : BOOL;
    loaded      : BOOL;
    comp_on     : BOOL := TRUE;    (* a PLC restart does not stop a running compressor *)
    stopped_for : TON;
  END_VAR

  enabled := INT_TO_BOOL(cell_enable);

  (* the run command, with the minimum stop time before a restart *)
  stopped_for(IN := NOT comp_on, PT := MIN_STOP);
  IF enabled AND comp_ready AND comp_cmd > 0 THEN
    IF NOT comp_on AND stopped_for.Q THEN
      comp_on := TRUE;
    END_IF;
  ELSE
    comp_on := FALSE;
  END_IF;

  (* the load/unload band on the transducer reading *)
  IF comp_on THEN
    IF air_p <= load_p THEN
      loaded := TRUE;
    END_IF;
    IF air_p >= unload_p THEN
      loaded := FALSE;
    END_IF;
  ELSE
    loaded := FALSE;
  END_IF;

  comp_run := comp_on;
  IF comp_on THEN
    IF loaded THEN
      comp_speed := 100;
    ELSE
      comp_speed := UNLOADED;
    END_IF;
  ELSE
    comp_speed := 0;
  END_IF;
  loaded_word := BOOL_TO_INT(loaded);

  chiller_run := enabled AND chiller_ready AND chiller_limit > 0;
  IF chiller_run THEN
    chiller_speed := LIMIT(0, chiller_limit, 100);
  ELSE
    chiller_speed := 0;
  END_IF;
END_PROGRAM

CONFIGURATION utilities_config
  RESOURCE res0 ON PLC
    TASK main_task(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM util WITH main_task : utilities;
  END_RESOURCE
END_CONFIGURATION
