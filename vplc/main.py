"""vplc: a virtual PLC with a protocol profile, not vendor firmware (FLEET.md section 6).

The runtime runs an IEC 61131-3 Structured Text task in a measured scan loop and serves the image
over real protocols:
  field port   Modbus TCP (plant-sim writes inputs and reads outputs)
  SCADA port   S7comm DB1 (siemens-s7-1200) or Modbus TCP (generic-iec)
  control port HTTP (/healthz, /state, PUT /task, POST /run, POST /stop)

Env:
  PLC_NAME      plc-<slug> (default plc-local)
  PROFILE       siemens-s7-1200 | generic-iec (default generic-iec)
  TASK_DIR      a directory with task.st and task.json. It wins over TASK_NAME.
  TASK_NAME     a library task from tasks/ (for example stamping-line)
  DEVICE_TOKEN  the X-Device-Token for control writes and enrollment. Empty turns control writes off.
  ENROLL_URL    default http://tag-server.plant.svc.cluster.local:9300/enroll. Empty turns enrollment off.
  PLC_HOST      the host SCADA should poll (default <PLC_NAME>.fleet.svc.cluster.local)
  FIELD_PORT    default 5020
  SCADA_PORT    default 102 for S7comm, 502 for Modbus
  CONTROL_PORT  default 8080
  BIND_HOST     the listen address for all ports (default 0.0.0.0)
"""
import os
import signal
import sys
import threading

import tasklib
from control import ControlServer
from enroll import Enroller
from modbus_server import FieldContext, ModbusServers, ScadaContext
from profiles import CONTROL_PORT, FIELD_PORT, NAME_RE, PROFILES
from runtime import VERSION, Runtime
from st import CompileError, compile_task

DEFAULT_ENROLL_URL = "http://tag-server.plant.svc.cluster.local:9300/enroll"
HONESTY = "virtual PLC with a protocol profile, not vendor firmware"


class ConfigError(ValueError):
    pass


def _port(env, key, default):
    raw = env.get(key)
    if raw in (None, ""):
        return default
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a port number, got '{raw}'") from None
    if not 0 < port < 65536:
        raise ConfigError(f"{key} must be between 1 and 65535, got {port}")
    return port


def config_from_env(env=None):
    """Read and check the environment. Return a dict. Raise ConfigError on bad values."""
    env = os.environ if env is None else env
    name = env.get("PLC_NAME") or "plc-local"
    if not NAME_RE.match(name):
        raise ConfigError(f"PLC_NAME '{name}' does not match {NAME_RE.pattern}")
    profile = env.get("PROFILE") or "generic-iec"
    if profile not in PROFILES:
        raise ConfigError(f"PROFILE '{profile}' is unknown, use one of {', '.join(PROFILES)}")
    cfg = {
        "name": name,
        "profile": profile,
        "task_dir": env.get("TASK_DIR") or "",
        "task_name": env.get("TASK_NAME") or "",
        "token": env.get("DEVICE_TOKEN") or "",
        "enroll_url": env.get("ENROLL_URL", DEFAULT_ENROLL_URL),
        "host": env.get("PLC_HOST") or f"{name}.fleet.svc.cluster.local",
        "bind": env.get("BIND_HOST") or "0.0.0.0",
        "field_port": _port(env, "FIELD_PORT", FIELD_PORT),
        "scada_port": _port(env, "SCADA_PORT", PROFILES[profile]["scada_port"]),
        "control_port": _port(env, "CONTROL_PORT", CONTROL_PORT),
    }
    ports = [cfg["field_port"], cfg["scada_port"], cfg["control_port"]]
    if len(set(ports)) != 3:
        raise ConfigError(f"FIELD_PORT, SCADA_PORT, and CONTROL_PORT must differ, got {ports}")
    return cfg


def boot_task(cfg):
    """Return (source, manifest, origin) for the boot task, or None when no task is configured.
    An optional ConfigMap that does not exist mounts as an empty directory. A TASK_DIR without
    task.st therefore falls back to TASK_NAME."""
    if cfg["task_dir"] and (os.path.exists(os.path.join(cfg["task_dir"], "task.st")) or not cfg["task_name"]):
        source, manifest = tasklib.read_task_dir(cfg["task_dir"])
        return source, manifest, f"TASK_DIR {cfg['task_dir']}"
    if cfg["task_name"]:
        source, manifest = tasklib.read_library_task(cfg["task_name"])
        return source, manifest, f"library task {cfg['task_name']}"
    return None


class Plc:
    """All the parts of one virtual PLC, wired together. start() and stop() are safe to call once."""

    def __init__(self, cfg, enroll_heartbeat_s=None):
        self.cfg = cfg
        self.rt = Runtime(cfg["name"], cfg["profile"])
        kw = {} if enroll_heartbeat_s is None else {"heartbeat_s": enroll_heartbeat_s}
        self.enroller = Enroller(self.rt, cfg["enroll_url"], cfg["token"], cfg["host"], cfg["scada_port"], **kw)
        self.rt.on_event = self.enroller.kick
        protocol = PROFILES[cfg["profile"]]["protocol"]
        bindings = [(FieldContext(self.rt), cfg["bind"], cfg["field_port"])]
        self.s7 = None
        if protocol == "s7comm":
            from s7_server import S7Db1Server
            self.s7 = S7Db1Server(self.rt, cfg["bind"], cfg["scada_port"], cfg["name"])
        else:
            bindings.append((ScadaContext(self.rt), cfg["bind"], cfg["scada_port"]))
        self.modbus = ModbusServers(bindings)
        self.control = ControlServer(self.rt, self.enroller, cfg["token"], cfg["bind"], cfg["control_port"])

    def start(self):
        self.modbus.start()
        if self.s7 is not None:
            self.s7.start()
        self.control.start()
        self.enroller.start()
        task = None
        try:
            task = boot_task(self.cfg)
        except (OSError, ValueError) as e:
            self.rt.boot_fault(f"boot task could not be read: {e}")
        if task is not None:
            source, manifest, origin = task
            try:
                compiled = compile_task(source)
            except CompileError as e:
                self.rt.boot_fault(f"boot task from {origin} failed to compile: {e}")
            else:
                self.rt.load(compiled, manifest)
                self.rt.run()
        self.rt.start()

    def stop(self):
        self.rt.halt()
        self.enroller.stop()
        self.control.stop()
        if self.s7 is not None:
            self.s7.stop()
        self.modbus.stop()

    def banner(self):
        cfg, rt = self.cfg, self.rt
        p = PROFILES[cfg["profile"]]
        task = rt.task.info() if rt.task else None
        task_txt = f"task {task['name']} ({task['interval_ms']} ms)" if task else "no task"
        return (f"vplc {VERSION} {cfg['name']} up | {HONESTY} | profile {cfg['profile']} ({p['label']}) | "
                f"scada {p['protocol']} :{cfg['scada_port']} | field modbus :{cfg['field_port']} | "
                f"control http :{cfg['control_port']} | {task_txt} | state {rt.state}")


def main():
    try:
        cfg = config_from_env()
    except ConfigError as e:
        print(f"vplc: bad configuration: {e}", file=sys.stderr, flush=True)
        return 2
    plc = Plc(cfg)
    plc.start()
    print(plc.banner(), flush=True)
    if plc.rt.fault:
        print(f"vplc {cfg['name']}: {plc.rt.fault}", flush=True)
    done = threading.Event()

    def on_signal(signum, frame):
        done.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    while not done.wait(1.0):
        pass
    plc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
