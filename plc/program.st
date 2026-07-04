(* plant_trips — the 2F trip interlocks (LOG-033).
   Real IEC 61131-3 running on OpenPLC; the plant-sim is the field wiring (plc/REGISTER_MAP.md).
   Latched thermal trips: a machine whose coolant-side temperature reaches TRIP (78.0 C, x10
   scaling) trips and STAYS tripped until an operator reset arrives AND the condition is gone —
   the classic interlock discipline. Nothing here is cosmetic: a set coil opens that machine's
   contactor in the sim, so PS5 has a real consequence and the Stage-3 act loop has a real
   actuator (the reset word). *)
PROGRAM plant_trips
  VAR
    t_press1   AT %MW0  : INT;   (* temp x10, press-1   *)
    t_press2   AT %MW1  : INT;   (* temp x10, press-2   *)
    t_cnc1     AT %MW2  : INT;   (* temp x10, cnc-1     *)
    t_furnace1 AT %MW3  : INT;   (* temp x10, furnace-1 *)
    reset_cmd  AT %MW20 : INT;   (* operator reset request; consumed (self-clearing) *)
    trip_press1   AT %QX0.0 : BOOL;
    trip_press2   AT %QX0.1 : BOOL;
    trip_cnc1     AT %QX0.2 : BOOL;
    trip_furnace1 AT %QX0.3 : BOOL;
  END_VAR
  VAR
    TRIP_X10 : INT := 780;       (* 78.0 C, matches the sim's TRIP_C *)
  END_VAR

  (* latch on threshold *)
  IF t_press1   >= TRIP_X10 THEN trip_press1   := TRUE; END_IF;
  IF t_press2   >= TRIP_X10 THEN trip_press2   := TRUE; END_IF;
  IF t_cnc1     >= TRIP_X10 THEN trip_cnc1     := TRUE; END_IF;
  IF t_furnace1 >= TRIP_X10 THEN trip_furnace1 := TRUE; END_IF;

  (* condition-gated reset: unlatch ONLY machines that have cooled below trip *)
  IF reset_cmd > 0 THEN
    IF t_press1   < TRIP_X10 THEN trip_press1   := FALSE; END_IF;
    IF t_press2   < TRIP_X10 THEN trip_press2   := FALSE; END_IF;
    IF t_cnc1     < TRIP_X10 THEN trip_cnc1     := FALSE; END_IF;
    IF t_furnace1 < TRIP_X10 THEN trip_furnace1 := FALSE; END_IF;
    reset_cmd := 0;
  END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#100ms,PRIORITY := 0);
    PROGRAM instance0 WITH task0 : plant_trips;
  END_RESOURCE
END_CONFIGURATION
