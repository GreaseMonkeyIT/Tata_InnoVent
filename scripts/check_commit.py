#!/usr/bin/env python3
"""The repository guard: nothing goes into git that must not be public (CONTRIBUTING.md).

Checks:
  1. Paths: no file under data/, references/, eval/cache/ or eval/results/, and no file of a kind
     that carries data, documents or binaries (see BANNED_EXT).
  2. Size: no file above MAX_KB.
  3. Added lines: no private key, no token of a known service, no credential in an assignment, no
     personal home path, no merge conflict marker. A line that must keep such text (a test fixture)
     carries the marker "guard: allow".

Modes:
  --staged      the files and the added lines of the staged change (before a commit)
  --range A..B  the files and the added lines between two commits (CI, on a push or a pull request)
  --tree        every tracked file, paths and sizes only (CI)

Exit status 1 with a list of the findings when a check fails. Standard library only.
"""
import argparse
import os
import re
import subprocess
import sys

MAX_KB = 500
BANNED_DIRS = ("data/", "references/", "eval/cache/", "eval/results/")
BANNED_EXT = (".csv", ".xlsx", ".xls", ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".mp4", ".mov", ".avi",
              ".npz", ".npy", ".pkl", ".h5", ".parquet", ".qcow2", ".tar", ".tgz", ".zip", ".7z")
BANNED_NAMES = (".env",)
LINE_RULES = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("credential in an assignment", re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{6,}['\"]")),
    ("personal home path", re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+(?!public\b|default\b)[A-Za-z0-9._-]+")),
    ("personal home path", re.compile(r"/home/(?!runner\b|node\b|app\b)[a-z_][a-z0-9_-]*")),
    ("merge conflict marker", re.compile(r"^(<<<<<<<|>>>>>>>) ")),
]
ALLOW = "guard: allow"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def path_findings(paths, sizes=True):
    out = []
    for p in paths:
        q = p.replace("\\", "/")
        if q.startswith(BANNED_DIRS):
            out.append((p, "a folder that stays out of git"))
        elif q.lower().endswith(BANNED_EXT) or os.path.basename(q) in BANNED_NAMES:
            out.append((p, "a file kind that stays out of git (data, documents, binaries)"))
        elif sizes and os.path.isfile(p) and os.path.getsize(p) > MAX_KB * 1024:
            out.append((p, f"larger than {MAX_KB} KB ({os.path.getsize(p) // 1024} KB)"))
    return out


def line_findings(diff):
    out, path = [], None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            continue
        if not raw.startswith("+") or raw.startswith("+++") or ALLOW in raw:
            continue
        line = raw[1:]
        for name, rx in LINE_RULES:
            if rx.search(line):
                out.append((path, f"{name}: {line.strip()[:100]}"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true")
    g.add_argument("--range")
    g.add_argument("--tree", action="store_true")
    a = ap.parse_args(argv)
    if a.tree:
        found = path_findings(git("ls-files").splitlines())
    else:
        spec = ["--cached"] if a.staged else a.range.split("..", 1)
        paths = git("diff", "--name-only", "--diff-filter=AMR", *spec).splitlines()
        found = path_findings(paths) + line_findings(git("diff", "-U0", "--no-color", *spec))
    for p, why in found:
        print(f"{p}: {why}")
    if found:
        print(f"\nThe guard found {len(found)} problem(s). CONTRIBUTING.md says what stays out of git.")
        return 1
    print("The guard found no problem.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
