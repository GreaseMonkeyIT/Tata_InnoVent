(* packaging-cell-rush: the packaging cell at rush cadence. Same wiring as packaging-cell.
   The wrapper cycles 10 s on and 5 s off, so the electrical load changes twice as often.
   The conveyor runs while the cell is enabled and the conveyor is READY. The labeler follows the
   wrapper. A CTU counts one pack each time a wrap cycle ends. *)
PROGRAM packaging_cell_rush
  VAR
    conveyor_ready  AT %IX0.0 : BOOL;
    wrapper_ready   AT %IX0.1 : BOOL;
    labeler_ready   AT %IX0.2 : BOOL;
    conveyor_run    AT %QX0.0 : BOOL;
    wrapper_run     AT %QX0.1 : BOOL;
    labeler_run     AT %QX0.2 : BOOL;
    conveyor_speed  AT %QW0   : INT;
    wrapper_speed   AT %QW1   : INT;
    labeler_speed   AT %QW2   : INT;
    cell_enable     AT %MW8   : INT := 1;
    conveyor_derate AT %MW10  : INT := 100;
    wrapper_derate  AT %MW11  : INT := 100;
    labeler_derate  AT %MW12  : INT := 100;
    pack_count      AT %MW20  : INT;
    step_word       AT %MW21  : INT;   (* 1 = wrapping, 2 = wrapper pause *)
  END_VAR
  VAR CONSTANT
    STEP_WRAP  : INT := 1;
    STEP_PAUSE : INT := 2;
    WRAP_ON    : TIME := T#10s;
    WRAP_OFF   : TIME := T#5s;
  END_VAR
  VAR
    step       : INT := 1;
    line_ok    : BOOL;
    wrap_time  : TON;
    pause_time : TON;
    packs      : CTU;
  END_VAR

  line_ok := INT_TO_BOOL(cell_enable) AND conveyor_ready;

  CASE step OF
    STEP_WRAP:
      wrap_time(IN := line_ok AND wrapper_ready, PT := WRAP_ON);
      IF wrap_time.Q THEN
        wrap_time(IN := FALSE);
        step := STEP_PAUSE;
      END_IF;
    STEP_PAUSE:
      pause_time(IN := line_ok, PT := WRAP_OFF);
      IF pause_time.Q THEN
        pause_time(IN := FALSE);
        step := STEP_WRAP;
      END_IF;
  ELSE
    step := STEP_WRAP;
  END_CASE;

  packs(CU := step = STEP_PAUSE, R := FALSE, PV := 32767);
  pack_count := packs.CV;
  step_word := step;

  conveyor_run := line_ok;
  wrapper_run := line_ok AND wrapper_ready AND step = STEP_WRAP;
  labeler_run := wrapper_run AND labeler_ready;

  IF conveyor_run THEN conveyor_speed := LIMIT(0, conveyor_derate, 100); ELSE conveyor_speed := 0; END_IF;
  IF wrapper_run THEN wrapper_speed := LIMIT(0, wrapper_derate, 100); ELSE wrapper_speed := 0; END_IF;
  IF labeler_run THEN labeler_speed := LIMIT(0, labeler_derate, 100); ELSE labeler_speed := 0; END_IF;
END_PROGRAM

CONFIGURATION packaging_rush_config
  RESOURCE res0 ON PLC
    TASK main_task(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM packaging WITH main_task : packaging_cell_rush;
  END_RESOURCE
END_CONFIGURATION
