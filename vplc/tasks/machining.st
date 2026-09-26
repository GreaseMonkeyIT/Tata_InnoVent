(* machining: the line PLC's interface to cnc-1 (LOG-100), machine k = 0 on rail psu-a.
   A CNC keeps its own controller for the part program, the spindle, and the doors. The line
   PLC sends it the handshake a cell uses: feed enable (off is a feed hold) and the feed override.
   RUN is feed enable. SPEED_PCT is the feed override in %, from DERATE_PCT (%MW10). A CNC allows
   150 % or more. The line keeps it at 0..100 %. FEED_HOLD (%MW21) = 1 holds the feed. *)
PROGRAM machining
  VAR
    cnc_ready    AT %IX0.0 : BOOL;
    cnc_run      AT %QX0.0 : BOOL;
    cnc_speed    AT %QW0   : INT;
    cell_enable  AT %MW8   : INT := 1;
    cnc_override AT %MW10  : INT := 100;
    feed_hold    AT %MW21  : INT := 0;
  END_VAR
  VAR
    enabled : BOOL;
  END_VAR

  enabled := INT_TO_BOOL(cell_enable) AND NOT INT_TO_BOOL(feed_hold);
  IF cnc_ready AND enabled THEN
    cnc_run := TRUE;
    cnc_speed := LIMIT(0, cnc_override, 100);
  ELSE
    cnc_run := FALSE;
    cnc_speed := 0;
  END_IF;
END_PROGRAM

CONFIGURATION machining_config
  RESOURCE res0 ON PLC
    TASK main_task(INTERVAL := T#100ms, PRIORITY := 0);
    PROGRAM mach WITH main_task : machining;
  END_RESOURCE
END_CONFIGURATION
