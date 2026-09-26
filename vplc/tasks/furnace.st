(* furnace: furnace-1's power controller interface (LOG-100), machine k = 0 on rail psu-b.
   RUN is heat enable. SPEED_PCT is the power limit in %, from DERATE_PCT (%MW10).
   HEAT_ENABLE (%MW21) = 0 turns the heat off. The coil cooling water trip stays in the separate
   OpenPLC interlock. NFPA 86 wants the excess-temperature limit on its own sensor and its own
   device, so it is never part of this controller. *)
PROGRAM furnace
  VAR
    furnace_ready AT %IX0.0 : BOOL;
    furnace_run   AT %QX0.0 : BOOL;
    furnace_power AT %QW0   : INT;
    cell_enable   AT %MW8   : INT := 1;
    power_limit   AT %MW10  : INT := 100;
    heat_enable   AT %MW21  : INT := 1;
  END_VAR
  VAR
    enabled : BOOL;
  END_VAR

  enabled := INT_TO_BOOL(cell_enable) AND INT_TO_BOOL(heat_enable);
  IF furnace_ready AND enabled THEN
    furnace_run := TRUE;
    furnace_power := LIMIT(0, power_limit, 100);
  ELSE
    furnace_run := FALSE;
    furnace_power := 0;
  END_IF;
END_PROGRAM

CONFIGURATION furnace_config
  RESOURCE res0 ON PLC
    TASK main_task(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM furn WITH main_task : furnace;
  END_RESOURCE
END_CONFIGURATION
