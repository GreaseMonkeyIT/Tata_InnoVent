"""Task sources and manifests (FLEET.md 5.2 and 5.3).

A task comes from one of two places:
  - TASK_DIR: a mounted directory with task.st and task.json (the ConfigMap <name>-task)
  - TASK_NAME: a library task baked into the image under tasks/ (<name>.json names its .st file)
"""
import json
import os
import re

import image as image_mod

LIBRARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")
TASK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class ManifestError(ValueError):
    """The manifest does not have the shape of FLEET.md 5.2."""


def read_task_dir(path):
    """Return (source, manifest) from a directory with task.st and an optional task.json."""
    st_path = os.path.join(path, "task.st")
    with open(st_path, encoding="utf-8") as f:
        source = f.read()
    manifest = {}
    json_path = os.path.join(path, "task.json")
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            manifest = json.load(f)
    return source, validate_manifest(manifest)


def read_library_task(name, library=LIBRARY_DIR):
    """Return (source, manifest) for a library task by name."""
    if not TASK_NAME_RE.match(name or ""):
        raise ManifestError(f"bad task name '{name}'")
    with open(os.path.join(library, f"{name}.json"), encoding="utf-8") as f:
        manifest = validate_manifest(json.load(f))
    st_file = os.path.basename(manifest.get("st") or f"{name}.st")
    with open(os.path.join(library, st_file), encoding="utf-8") as f:
        source = f.read()
    return source, manifest


def list_library(library=LIBRARY_DIR):
    """Return the library manifests, sorted by task name."""
    out = []
    for fn in sorted(os.listdir(library)):
        if fn.endswith(".json"):
            with open(os.path.join(library, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def validate_manifest(manifest):
    """Check the fields the runtime uses. Return the manifest. Raise ManifestError."""
    if manifest is None:
        return {}
    if not isinstance(manifest, dict):
        raise ManifestError("the manifest must be a JSON object")
    for key in ("task", "title", "description", "st", "profile_hint"):
        if key in manifest and not isinstance(manifest[key], str):
            raise ManifestError(f"manifest field '{key}' must be a string")
    cell = manifest.get("cell")
    if cell is not None:
        if not isinstance(cell, dict):
            raise ManifestError("manifest field 'cell' must be an object")
        if "machines" in cell and not isinstance(cell["machines"], list):
            raise ManifestError("manifest field 'cell.machines' must be a list")
        if "machines_fixed" in cell and not (isinstance(cell["machines_fixed"], list)
                                             and all(isinstance(m, str) for m in cell["machines_fixed"])):
            raise ManifestError("manifest field 'cell.machines_fixed' must be a list of names")
        machines = cell.get("machines_fixed") or cell.get("machines") or []
        if len(machines) > 8:
            raise ManifestError("a cell has at most 8 machines (FLEET.md 3.1)")
    extra = manifest.get("io_extra")
    if extra is not None:
        if not isinstance(extra, list):
            raise ManifestError("manifest field 'io_extra' must be a list")
        for i, row in enumerate(extra):
            if not isinstance(row, dict) or not isinstance(row.get("address"), str):
                raise ManifestError(f"io_extra[{i}] needs an 'address' string")
            try:
                addr = image_mod.parse_address(row["address"])
            except ValueError as e:
                raise ManifestError(f"io_extra[{i}]: {e}") from None
            if addr.area == "MW" and addr.index < image_mod.SYSTEM_WORDS:
                raise ManifestError(f"io_extra[{i}]: {addr.text} is a system word")
    return manifest


def cell_summary(manifest):
    """The cell object of the enrollment body: {name, rail, machines: [names]}.

    FLEET.md 5.2 does not say where the API writes the machine names it chose. This reads, in order:
    cell.machines_fixed (base cells), then each machine's 'name', then its 'prefix'. The rail is
    cell.rail, then cell.rail_default."""
    cell = (manifest or {}).get("cell") or {}
    if cell.get("machines_fixed"):
        names = list(cell["machines_fixed"])
    else:
        names = []
        for m in cell.get("machines") or []:
            if isinstance(m, dict):
                n = m.get("name") or m.get("prefix")
                if n:
                    names.append(n)
            elif isinstance(m, str):
                names.append(m)
    return {"name": cell.get("name"), "rail": cell.get("rail") or cell.get("rail_default"), "machines": names}
