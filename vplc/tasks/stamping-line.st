(* stamping-line: the base stamping cell (press-1, press-2 on rail psu-a).
   Both presses run all the time. Each press runs at SPEED_PCT = LIMIT(0, DERATE_PCT, 100)
   while it is READY (not tripped) and CELL_ENABLE is 1. Otherwise RUN is off and SPEED_PCT is 0.
   Wiring follows FLEET.md 3.1 with machine index k: press-1 is k = 0, press-2 is k = 1. *)
PROGRAM stamping_line
  VAR
    press1_ready  AT %IX0.0 : BOOL;
    press2_ready  AT %IX0.1 : BOOL;
    press1_amps   AT %IW0   : INT;   (* A x10 *)
    press1_temp   AT %IW1   : INT;   (* degC x10 *)
    press1_volts  AT %IW2   : INT;   (* V x10 *)
    press1_thru   AT %IW3   : INT;   (* pct x10 *)
    press2_amps   AT %IW4   : INT;
    press2_temp   AT %IW5   : INT;
    press2_volts  AT %IW6   : INT;
    press2_thru   AT %IW7   : INT;
    press1_run    AT %QX0.0 : BOOL;
    press2_run    AT %QX0.1 : BOOL;
    press1_speed  AT %QW0   : INT;
    press2_speed  AT %QW1   : INT;
    cell_enable   AT %MW8   : INT := 1;
    press1_derate AT %MW10  : INT := 100;
    press2_derate AT %MW11  : INT := 100;
    running_count AT %MW20  : INT;   (* presses commanded to run this scan *)
  END_VAR
  VAR
    enabled : BOOL;
  END_VAR

  enabled := INT_TO_BOOL(cell_enable);

  IF press1_ready AND enabled THEN
    press1_run := TRUE;
    press1_speed := LIMIT(0, press1_derate, 100);
  ELSE
    press1_run := FALSE;
    press1_speed := 0;
  END_IF;

  IF press2_ready AND enabled THEN
    press2_run := TRUE;
    press2_speed := LIMIT(0, press2_derate, 100);
  ELSE
    press2_run := FALSE;
    press2_speed := 0;
  END_IF;

  running_count := BOOL_TO_INT(press1_run) + BOOL_TO_INT(press2_run);
END_PROGRAM

CONFIGURATION stamping_config
  RESOURCE res0 ON PLC
    TASK main_task(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM stamping WITH main_task : stamping_line;
  END_RESOURCE
END_CONFIGURATION
