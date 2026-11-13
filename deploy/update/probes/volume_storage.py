"""Fixed RO named-volume capacity/usage probe. No image code or supplied paths.

One already-owned named volume is mounted at /observed-volume. No bind mounts,
Docker socket, network, follow-links, cross-filesystem walk or writes. Inventory
bounds make an oversized/unreadable/unstable tree unknown, never zero bytes.
Allocated blocks are NOT logical backup/restore work: sparse files count at their
full apparent size, hardlinked payloads once, and all pathnames count as members.
"""
import hashlib
import json
import os
import signal
import stat
import sys

ROOT = "/observed-volume"
MAX_MEMBERS = 1000000
MAX_BYTES = 9007199254740991


def require(value):
    if not value:
        raise ValueError("restore_capacity_unknown")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def filesystem(info):
    with open("/proc/self/mountinfo", "rb") as handle:
        raw = handle.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024)
    found = []
    for line in raw.decode().splitlines():
        fields = line.split()
        middle = fields.index("-")
        if fields[4] == ROOT:
            require(fields[2] == "%d:%d" % (os.major(info.st_dev), os.minor(info.st_dev))
                    and fields[middle + 1] in ("ext4", "xfs", "btrfs")
                    and "ro" in fields[5].split(","))
            found.append({"device": fields[2], "filesystem": fields[middle + 1],
                          "source": fields[middle + 2]})
    require(len(found) == 1)
    result = found[0]
    return result["device"], result["filesystem"], hashlib.sha256(canonical(result)).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_blocks,
            info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def tree_sizes(root_fd):
    """Bounded single traversal, using the same payload/member semantics as tar.

    Include the root and every pathname in members, but count hardlinked payload
    and allocated blocks only once. Symlinks are metadata, never followed. A
    concurrent size/namespace change refuses this sample instead of understating
    work. This is a live pre-outage estimate, not a cold inventory/seal.
    """
    root = os.fstat(root_fd)
    device = root.st_dev
    seen = {(root.st_dev, root.st_ino): _identity(root)}
    members, allocated, logical = 1, root.st_blocks * 512, 0

    def visit(fd):
        nonlocal members, allocated, logical
        directory_before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                members += 1
                require(members <= MAX_MEMBERS)
                before = entry.stat(follow_symlinks=False)
                require(before.st_dev == device and (stat.S_ISREG(before.st_mode)
                        or stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode)))
                key = (before.st_dev, before.st_ino)
                identity = _identity(before)
                if key in seen:
                    require(seen[key] == identity)
                    continue
                seen[key] = identity
                require(before.st_blocks >= 0 and before.st_size >= 0)
                allocated += before.st_blocks * 512
                if stat.S_ISREG(before.st_mode):
                    logical += before.st_size
                require(0 <= allocated <= MAX_BYTES and 0 <= logical <= MAX_BYTES)
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        require(_identity(os.fstat(child)) == identity)
                        visit(child)
                    finally:
                        os.close(child)
                require(_identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)) == identity)
        require(_identity(os.fstat(fd)) == _identity(directory_before))

    require(1 <= MAX_MEMBERS and 0 <= allocated <= MAX_BYTES)
    visit(root_fd)
    return {"used_bytes": allocated, "logical_bytes": logical, "members": members}


def used_bytes(root_fd):
    """Allocated-byte diagnostic retained for existing callers; NOT restore work."""
    return tree_sizes(root_fd)["used_bytes"]


def observe(measure_used=True):
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        capacity = os.fstatvfs(fd)
        require(capacity.f_flag & os.ST_RDONLY)
        device, kind, backing = filesystem(before)
        sizes = tree_sizes(fd) if measure_used else {"used_bytes": None, "logical_bytes": None, "members": None}
        after = os.stat(ROOT, follow_symlinks=False)
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino))
        return {"schema_version": 2, "root_identity": "%d:%d" % (before.st_dev, before.st_ino),
                "device": device, "filesystem": kind, "backing_sha256": backing,
                "free_bytes": capacity.f_bavail * capacity.f_frsize,
                "free_inodes": capacity.f_favail, **sizes}
    finally:
        os.close(fd)


if __name__ == "__main__":
    signal.alarm(45)
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in ("sizes", "identity"))
        print(canonical(observe(sys.argv[1] == "sizes")).decode())
    except Exception:
        sys.exit(2)
