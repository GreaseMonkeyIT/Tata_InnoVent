#!/usr/bin/env python3
"""Copy the Grafana image of the paused k3s stack into Docker (LOG-122).

The console preview (deploy/console-preview.sh) runs Grafana as a Docker container. The main stack
has the image in the containerd store of k3s. While k3s is stopped, `k3s ctr` cannot reach that
store, and starting k3s would relaunch the whole stack. This script reads the store files directly,
read-only, and loads the image into Docker:

    sudo python3 ~/VISR_Overhaul/deploy/k3s-images-to-docker.py

It needs root only to read /var/lib/rancher/k3s. It writes an OCI archive to /var/tmp, loads it
with `docker load`, tags it visr-preview/grafana:k3s, and deletes the archive. It changes nothing in
the k3s store and does not start k3s.

How it finds the image: it reads every image manifest in the content store, then the image config.
Grafana has GF_PATHS_HOME in its environment. If several versions are in the store, it takes the
newest (by the config's "created" time). The Prometheus image of the store could not be used
(containerd had dropped its packed layers after unpacking), and the preview needs no Prometheus.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

STORE = "/var/lib/rancher/k3s/agent/containerd/io.containerd.content.v1.content/blobs/sha256"
MANIFESTS = {"application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"}
WANT = {
    "grafana": lambda c: any(e.startswith("GF_PATHS_HOME=") for e in (c.get("Env") or [])),
}


def blob(digest):
    return os.path.join(STORE, digest.split(":", 1)[1])


def read_json(path, limit=4 << 20):
    if os.path.getsize(path) > limit:
        return None
    try:
        with open(path, "rb") as f:
            return json.loads(f.read())
    except (ValueError, UnicodeDecodeError):
        return None


def find():
    """{name: (created, manifest digest, manifest)} for the newest image of each kind."""
    found = {}
    for h in os.listdir(STORE):
        m = read_json(os.path.join(STORE, h))
        if not isinstance(m, dict) or m.get("mediaType") not in MANIFESTS and "layers" not in m:
            continue
        cfg_d = (m.get("config") or {}).get("digest")
        if not cfg_d or not os.path.exists(blob(cfg_d)):
            continue
        cfg = read_json(blob(cfg_d))
        if not isinstance(cfg, dict) or cfg.get("architecture") not in (None, "amd64"):
            continue
        for name, test in WANT.items():
            if test(cfg.get("config") or {}):
                created = cfg.get("created", "")
                if name not in found or created > found[name][0]:
                    found[name] = (created, "sha256:" + h, m)
    return found


def export(name, digest, manifest, out):
    """Write an OCI image layout archive of one image; return the list of missing blobs."""
    need = [digest, manifest["config"]["digest"]] + [l["digest"] for l in manifest.get("layers", [])]
    missing = [d for d in need if not os.path.exists(blob(d))]
    if missing:
        return missing
    ref = f"visr-preview/{name}:k3s"
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
             "manifests": [{"mediaType": manifest.get("mediaType", "application/vnd.oci.image.manifest.v1+json"),
                            "digest": digest, "size": os.path.getsize(blob(digest)),
                            "annotations": {"io.containerd.image.name": f"docker.io/{ref}", "org.opencontainers.image.ref.name": "k3s"}}]}
    with tarfile.open(out, "w") as tar:
        def add_bytes(path, data):
            ti = tarfile.TarInfo(path); ti.size = len(data); ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))
        add_bytes("oci-layout", json.dumps({"imageLayoutVersion": "1.0.0"}).encode())
        add_bytes("index.json", json.dumps(index).encode())
        for d in need:
            tar.add(blob(d), arcname="blobs/sha256/" + d.split(":", 1)[1])
    return []


def main():
    if not os.path.isdir(STORE):
        sys.exit(f"no containerd content store at {STORE} (run with sudo)")
    found = find()
    for name in WANT:
        if name not in found:
            sys.exit(f"no {name} image in the k3s store: pull it from Docker Hub instead")
    for name, (created, digest, manifest) in found.items():
        fd, out = tempfile.mkstemp(prefix=f"visr-{name}-", suffix=".tar", dir="/var/tmp")
        os.close(fd)
        try:
            missing = export(name, digest, manifest, out)
            if missing:
                sys.exit(f"{name}: {len(missing)} blobs are not in the k3s store (containerd dropped the "
                         "packed layers after unpacking): pull the image from Docker Hub instead")
            r = subprocess.run(["docker", "load", "-i", out], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            if r.returncode:
                sys.exit(f"{name}: docker load failed")
            # docker load names the image from the annotation; tag it by ID as well, to be sure
            ids = [l.split()[-1] for l in r.stdout.splitlines() if "Loaded image ID:" in l]
            for i in ids:
                subprocess.run(["docker", "tag", i, f"visr-preview/{name}:k3s"], check=False)
            print(f"{name}: visr-preview/{name}:k3s (created {created})")
        finally:
            os.remove(out)


if __name__ == "__main__":
    main()
