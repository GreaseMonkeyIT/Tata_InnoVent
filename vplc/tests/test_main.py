"""Boot configuration (FLEET.md 6): env checks and the boot task choice."""
import json

import pytest

from main import ConfigError, boot_task, config_from_env


def test_profile_picks_the_scada_port():
    assert config_from_env({"PLC_NAME": "plc-a", "PROFILE": "siemens-s7-1200"})["scada_port"] == 102
    assert config_from_env({"PLC_NAME": "plc-a", "PROFILE": "generic-iec"})["scada_port"] == 502


def test_bad_name_unknown_profile_and_clashing_ports_are_refused():
    with pytest.raises(ConfigError):
        config_from_env({"PLC_NAME": "PLC_A"})
    with pytest.raises(ConfigError):
        config_from_env({"PLC_NAME": "plc-a", "PROFILE": "nope"})
    with pytest.raises(ConfigError):
        config_from_env({"PLC_NAME": "plc-a", "FIELD_PORT": "502", "PROFILE": "generic-iec"})


def test_empty_task_dir_falls_back_to_the_library_task(tmp_path):
    cfg = config_from_env({"PLC_NAME": "plc-stamping", "TASK_DIR": str(tmp_path), "TASK_NAME": "stamping-line"})
    source, manifest, origin = boot_task(cfg)
    assert manifest["task"] == "stamping-line" and "library" in origin


def test_a_loaded_task_dir_wins_over_the_library(tmp_path):
    (tmp_path / "task.st").write_text("PROGRAM p VAR y AT %QW0 : INT; END_VAR y := 1; END_PROGRAM", encoding="utf-8")
    (tmp_path / "task.json").write_text(json.dumps({"task": "custom"}), encoding="utf-8")
    cfg = config_from_env({"PLC_NAME": "plc-stamping", "TASK_DIR": str(tmp_path), "TASK_NAME": "stamping-line"})
    source, manifest, origin = boot_task(cfg)
    assert manifest["task"] == "custom" and "TASK_DIR" in origin
