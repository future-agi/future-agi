"""Fixed read-only Linux daemon-store probe, delivered on stdin. No Docker socket.

Only the externally managed containerd default layout is currently supported.
A store's directory name alone is NOT evidence: the live dockerd and containerd
argv/config/mount namespaces must establish that both stores are on this device.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys


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


def processes(name):
    found = []
    for path in Path("/host-proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            if read(path / "comm", 256).strip().decode() == name:
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


def config_before_start(proc, path):
    # A config edited after process start does not prove the running root.
    # Reject that ambiguity rather than interpreting a freshly edited file as
    # the daemon's loaded configuration. Linux starttime is field 22 of stat.
    fields = read(proc / "stat").decode().rsplit(")", 1)[1].split()
    boot = next(int(line.split()[1]) for line in read("/host-proc/stat").decode().splitlines() if line.startswith("btime "))
    started = boot + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    require(Path(path).stat().st_mtime <= started)
    return read(path)


def observe():
    import tomllib  # The preprovisioned helper is Python >=3.11, NOT the host.

    dockerd, docker_args = processes("dockerd")
    containerd, container_args = processes("containerd")
    require(not any(word.startswith(("-c", "-r", "-a")) and not word.startswith("--") for word in container_args))
    require(not any(word.startswith("-g") or word == "--graph" or word.startswith("--graph=") for word in docker_args))
    require(option(docker_args, "--containerd") == "/run/containerd/containerd.sock")
    require(option(docker_args, "--data-root", "/var/lib/docker") == "/var/lib/docker")
    require(option(docker_args, "--config-file", "/etc/docker/daemon.json") == "/etc/docker/daemon.json")
    # Dockerd's CURRENT data root/driver/containerd address come directly
    # from the authenticated live info API (checked by the host). Its config
    # can be rewritten without changing those loaded values. Containerd's root
    # is NOT in that API, so its config must independently predate its start.
    docker_raw = read("/host-docker-config/daemon.json")
    docker_config = json.loads(docker_raw)
    require(docker_config.get("data-root", "/var/lib/docker") == "/var/lib/docker")
    require(docker_config.get("containerd", "/run/containerd/containerd.sock") == "/run/containerd/containerd.sock")
    require(option(container_args, "--config", "/etc/containerd/config.toml") == "/etc/containerd/config.toml")
    require(option(container_args, "--root", "/var/lib/containerd") == "/var/lib/containerd")
    container_raw = config_before_start(containerd, "/host-containerd-config/config.toml")
    config = tomllib.loads(container_raw.decode())
    require(config.get("root", "/var/lib/containerd") == "/var/lib/containerd")
    require(config.get("state", "/run/containerd") == "/run/containerd")
    require(config.get("grpc", {}).get("address", "/run/containerd/containerd.sock") == "/run/containerd/containerd.sock")
    require(option(container_args, "--address", "/run/containerd/containerd.sock") == "/run/containerd/containerd.sock")
    require(not config.get("imports") and not config.get("proxy_plugins"))
    # A plugin-specific root would invalidate the standard directory mapping.
    for value in config.get("plugins", {}).values():
        require(not any(key in value for key in ("root", "root_path", "path")))
    stores = {}
    for name, relative in (("content", "io.containerd.content.v1.content/blobs/sha256"),
                           ("snapshots", "io.containerd.snapshotter.v1.overlayfs/snapshots")):
        actual = "/var/lib/containerd/" + relative
        mounted = Path("/daemon-store") / relative
        for parent in (Path("/daemon-store"), *list(mounted.parents)[:-1], mounted):
            require(not parent.is_symlink())
        info = mounted.stat()
        require(stat.S_ISDIR(info.st_mode))
        mount = mount_for(containerd, actual)
        device = "{}:{}".format(os.major(info.st_dev), os.minor(info.st_dev))
        require(mount[1] == device and mount[3] in ("ext4", "xfs", "btrfs"))
        require(mount_for(dockerd, actual)[1:] == mount[1:])
        capacity = os.statvfs(mounted)
        require(capacity.f_flag & os.ST_RDONLY)
        stores[name] = {"device": device, "filesystem": mount[3],
                        "backing_sha256": hashlib.sha256(json.dumps(mount[1:]).encode()).hexdigest(),
                        "available_bytes": capacity.f_bavail * capacity.f_frsize}
    require(processes("dockerd") == (dockerd, docker_args) and processes("containerd") == (containerd, container_args))
    require(read("/host-docker-config/daemon.json") == docker_raw
            and config_before_start(containerd, "/host-containerd-config/config.toml") == container_raw)
    return {"schema_version": 1, "backend": "external-containerd-overlayfs", "stores": stores}


if __name__ == "__main__":
    signal.alarm(15)
    try:
        print(json.dumps(observe(), sort_keys=True, separators=(",", ":")))
    except Exception:
        # Never print /proc contents, configuration or raw exceptions.
        sys.exit(2)
