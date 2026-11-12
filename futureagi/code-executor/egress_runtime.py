"""Unprivileged, fail-closed witness for the init-installed nftables policy.

A sealed memfd records the verified policy and network namespace. A netfilter
multicast socket subscribes BEFORE installation and is drained BEFORE readback.
After readback it is never consumed: any subsequent ruleset event or socket
error permanently makes every worker unhealthy. This avoids giving the server
NET_ADMIN just to list nftables, or trusting an on-disk readiness marker.
"""

import ctypes
import fcntl
import hashlib
import json
import os
import select
import socket
import stat

from egress_guard import validate_runtime_status

NET_ADMIN = 1 << 12
NETLINK_NETFILTER = 12
NFNLGRP_NFTABLES = 7
SEALS = 0x01 | 0x02 | 0x04 | 0x08  # SEAL, SHRINK, GROW, WRITE
F_ADD_SEALS = 1033
F_GET_SEALS = 1034


def process_status():
    with open("/proc/self/status", encoding="ascii") as stream:
        return dict(line.rstrip().split(":", 1) for line in stream if ":" in line)


def namespace_identity():
    info = os.stat("/proc/self/ns/net")
    return [info.st_dev, info.st_ino]


def open_monitor():
    monitor = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_NETFILTER)
    try:
        monitor.bind((0, 1 << (NFNLGRP_NFTABLES - 1)))
        monitor.setblocking(False)
        return monitor
    except BaseException:
        monitor.close()
        raise


def drain_install_events(monitor):
    # A notification loss (ENOBUFS) is an error, never treated as an empty queue.
    for _ in range(1024):
        try:
            packet = monitor.recv(65536)
            if not packet:
                raise RuntimeError("Closed policy monitor")
        except BlockingIOError:
            return
    raise RuntimeError("Policy notification queue did not settle")


def assert_monitor_quiet(fd):
    if not stat.S_ISSOCK(os.fstat(fd).st_mode):
        raise RuntimeError("Missing policy monitor socket")
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
    if poller.poll(0):
        # NEVER read here: leave the event/error pending for all gunicorn workers.
        raise RuntimeError("Policy changed after verification")


def seal_witness(script, monitor_fd):
    fd = os.memfd_create("executor-policy", os.MFD_ALLOW_SEALING)
    try:
        data = json.dumps(
            {
                "policy": hashlib.sha256(script.encode()).hexdigest(),
                "namespace": namespace_identity(),
                "monitor": monitor_fd,
            }
        ).encode()
        if os.write(fd, data) != len(data):
            raise RuntimeError("Short policy witness write")
        fcntl.fcntl(fd, F_ADD_SEALS, SEALS)
        os.set_inheritable(fd, True)
        os.set_inheritable(monitor_fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def verify_policy(script):
    validate_runtime_status(process_status())
    fd = int(os.environ["CODE_EXECUTOR_POLICY_FD"])
    if fd <= 2 or fcntl.fcntl(fd, F_GET_SEALS) & SEALS != SEALS:
        raise RuntimeError("Policy witness is missing or mutable")
    witness = json.loads(os.pread(fd, 8192, 0))
    if witness != {
        "policy": hashlib.sha256(script.encode()).hexdigest(),
        "namespace": namespace_identity(),
        "monitor": witness.get("monitor"),
    }:
        raise RuntimeError("Policy witness does not match runtime")
    monitor_fd = witness["monitor"]
    if type(monitor_fd) is not int or monitor_fd <= 2 or monitor_fd == fd:
        raise RuntimeError("Invalid policy monitor")
    assert_monitor_quiet(monitor_fd)


def drop_capabilities():
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [
            ("effective", ctypes.c_uint32),
            ("permitted", ctypes.c_uint32),
            ("inheritable", ctypes.c_uint32),
        ]

    libc = ctypes.CDLL(None, use_errno=True)
    header = Header(0x20080522, 0)
    empty = (Data * 2)()
    # NNP is inherited by gunicorn and all eval descendants. Even the init
    # file capability can never be reacquired by submitted code.
    for option, arg in ((38, 1), (47, 4)):  # SET_NO_NEW_PRIVS, AMBIENT_CLEAR_ALL
        if (
            libc.prctl(
                ctypes.c_int(option),
                ctypes.c_ulong(arg),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
            )
            != 0
        ):
            raise OSError(ctypes.get_errno(), "Cannot lock executor privileges")
    if libc.capset(ctypes.byref(header), ctypes.byref(empty)) != 0:
        raise OSError(ctypes.get_errno(), "Cannot drop executor capabilities")
    validate_runtime_status(process_status())
