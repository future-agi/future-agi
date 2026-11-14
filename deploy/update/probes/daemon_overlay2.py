"""Fixed read-only probe for rootful classic overlay2, delivered on stdin. No Docker socket.

An unprivileged installation user cannot read a root dockerd's mount namespace
or traverse /var/lib/docker, so the host measures image-store capacity from the
daemon side. The host has already verified Driver=overlay2 and
DockerRootDir=/var/lib/docker through the registered daemon's live API. This
probe proves the bound store is on the device that the running dockerd mounts at
its overlay2 directory, then measures that filesystem. It reads only
world-readable /proc entries (comm, cmdline, mountinfo), never ns/ or root.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys

STORE = "/var/lib/docker/overlay2"


def require(value):
    if not value:
        raise ValueError("unsupported")


def read(path, limit=65536):
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    require(len(raw) <= limit)
    return raw


def option(argv, name, default=None):
    values = []
    for index, word in enumerate(argv):
        if word == name:
            require(index + 1 < len(argv))
            values.append(argv[index + 1])
        elif word.startswith(name + "="):
            values.append(word[len(name) + 1:])
    require(len(values) <= 1)
    return values[0] if values else default


def dockerd():
    found = []
    for path in Path("/host-proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            if read(path / "comm", 256).strip().decode() == "dockerd":
                argv = read(path / "cmdline").rstrip(b"\0").decode().split("\0")
                found.append((path, argv))
        except (FileNotFoundError, ProcessLookupError):
            continue
    require(len(found) == 1)
    return found[0]


def mount_for(proc, path):
    # Paths in the qualified layout contain neither whitespace nor escapes.
    rows = []
    for line in read(proc / "mountinfo", 1024 * 1024).decode().splitlines():
        fields = line.split()
        middle = fields.index("-")
        point = fields[4]
        if path == point or path.startswith(point.rstrip("/") + "/"):
            rows.append((len(point), fields[2], point, fields[middle + 1], fields[middle + 2]))
    require(bool(rows))
    return max(rows)


def observe():
    proc, argv = dockerd()
    require(option(argv, "--data-root", "/var/lib/docker") == "/var/lib/docker")
    require(not any(word.startswith("-g") or word == "--graph" or word.startswith("--graph=") for word in argv))
    store = Path("/daemon-store")
    require(not store.is_symlink())
    info = store.stat()
    require(stat.S_ISDIR(info.st_mode))
    mount = mount_for(proc, STORE)
    device = "{}:{}".format(os.major(info.st_dev), os.minor(info.st_dev))
    require(mount[1] == device and mount[3] in ("ext4", "xfs", "btrfs"))
    capacity = os.statvfs(store)
    require(capacity.f_flag & os.ST_RDONLY)
    stores = {"overlay2": {"device": device, "filesystem": mount[3],
                           "backing_sha256": hashlib.sha256(json.dumps(mount[1:]).encode()).hexdigest(),
                           "available_bytes": capacity.f_bavail * capacity.f_frsize}}
    require(dockerd() == (proc, argv))
    return {"schema_version": 1, "backend": "helper-overlay2", "stores": stores}


if __name__ == "__main__":
    signal.alarm(15)
    try:
        print(json.dumps(observe(), sort_keys=True, separators=(",", ":")))
    except Exception:
        # Never print /proc contents, mount tables or raw exceptions.
        sys.exit(2)
