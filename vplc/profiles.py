"""Protocol profiles and default ports (FLEET.md section 2).

A profile picks the SCADA protocol and its port. It does not change the image or the task
language. Every profile is a virtual PLC with a protocol profile, not vendor firmware.
"""
import re

FIELD_PORT = 5020
CONTROL_PORT = 8080

PROFILES = {
    "siemens-s7-1200": {"label": "Siemens S7-1200 (virtual)", "protocol": "s7comm", "scada_port": 102,
                        "rack": 0, "slot": 1, "db": 1},
    "generic-iec": {"label": "IEC 61131-3 soft PLC", "protocol": "modbus", "scada_port": 502},
}

NAME_RE = re.compile(r"^plc-[a-z0-9]([-a-z0-9]{0,16}[a-z0-9])?$")


def protocol_block(profile_id, host, scada_port):
    """The protocol object of the enrollment body."""
    p = PROFILES[profile_id]
    if p["protocol"] == "s7comm":
        return {"kind": "s7comm", "host": host, "port": scada_port,
                "rack": p["rack"], "slot": p["slot"], "db": p["db"]}
    return {"kind": "modbus", "host": host, "port": scada_port, "rack": None, "slot": None, "db": None}
