"""Kernel egress boundary shared by nsjail and subprocess evals.

Trusted init installs and reads back an atomic nftables policy before execing
an unprivileged server. Runtime admission verifies the sealed policy witness
and change monitor; it never reacquires NET_ADMIN. The uid-scoped policy allows
only the server's control replies and health probes in addition to eval egress.
The child runs without root, supplementary groups or privilege gain;
seccomp forbids non-IP sockets and io_uring (including Unix-socket bypasses).
No Python/JS monkeypatch is a security boundary.

Init requires Linux, nft and CAP_NET_ADMIN in the container network namespace.
Missing support refuses execution, never runs unrestricted. CODE_EXECUTOR_INTERNAL_CIDRS
adds deployment-specific pod/service/node ranges, including publicly numbered
internal networks. DNS is limited to TCP/UDP port 53 at the literal resolvers
in resolv.conf; resolved destinations still pass the same address/port policy.
"""

import ctypes
import errno
import ipaddress
import os
import platform
import shutil
import subprocess
import sys

EVAL_UID = 65534
# Special-use, private, shared, loopback, metadata, multicast and reserved space.
DENIED_CIDRS = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::/96",
    "::ffff:0:0/96",
    "64:ff9b::/96",
    "64:ff9b:1::/48",
    "100::/64",
    "2001::/23",
    "2001:db8::/32",
    "2002::/16",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
)


def _resolver_address(value):
    # A scope suffix is not a literal nft address; never interpolate it into
    # rules. Unspecified and multicast addresses cannot be unicast resolvers.
    if "%" in value:
        raise ValueError("Scoped DNS resolver is unsupported")
    address = ipaddress.ip_address(value)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Invalid DNS resolver")
    return str(address)


def read_resolvers():
    """Read only literal nameservers, never resolve names or fall back to any DNS."""
    resolvers = []
    with open("/etc/resolv.conf", encoding="utf-8") as stream:
        for line in stream:
            fields = line.split("#", 1)[0].split(";", 1)[0].split()
            if not fields or fields[0] != "nameserver":
                continue
            if len(fields) != 2:
                raise ValueError("Invalid nameserver directive")
            address = _resolver_address(fields[1])
            if address not in resolvers:
                resolvers.append(address)
    if not resolvers:
        raise ValueError("No configured DNS resolver")
    return tuple(resolvers)


def policy_rules(ports, internal_cidrs=(), resolvers=()):
    ports = tuple(sorted(set(ports)))
    if not ports or any(type(p) is not int or not 0 < p < 65536 for p in ports):
        raise ValueError("Invalid egress ports")
    parsed = [
        ipaddress.ip_network(cidr, strict=True)
        for cidr in (*DENIED_CIDRS, *internal_cidrs)
    ]
    # Cluster ranges commonly overlap RFC1918. nft interval sets must not
    # contain conflicting/duplicate intervals, so canonicalize each family.
    networks = tuple(
        network
        for version in (4, 6)
        for network in ipaddress.collapse_addresses(
            n for n in parsed if n.version == version
        )
    )
    return {
        # Port 53 is reserved exclusively for the resolver exception, even if
        # the deployment includes it in the general public-egress setting.
        "ports": tuple(port for port in ports if port != 53),
        "networks": networks,
        "resolvers": tuple(dict.fromkeys(_resolver_address(r) for r in resolvers)),
    }


def allows_destination(rules, address, port, transport="tcp"):
    """Inspect original-direction connects; enforcement is always nftables."""
    ip = ipaddress.ip_address(address)
    if transport not in ("tcp", "udp"):
        return False
    if str(ip) == "127.0.0.1" and port == 8060 and transport == "tcp":
        return True
    if port == 53:
        return str(ip) in rules["resolvers"]
    if any(ip.version == net.version and ip in net for net in rules["networks"]):
        return False
    if ip.version == 6 and ip not in ipaddress.ip_network("2000::/3"):
        return False
    return port in rules["ports"]


def nft_script(rules):
    """One atomic transaction, also safe for concurrent gunicorn workers."""
    prefix = f"add rule inet fa_code_eval output meta skuid {EVAL_UID}"
    lines = [
        "add table inet fa_code_eval",
        "add chain inet fa_code_eval output { type filter hook output priority 0; policy accept; }",
        "flush chain inet fa_code_eval output",
    ]
    for version, family in ((4, "ip"), (6, "ip6")):
        resolvers = ", ".join(
            address
            for address in rules["resolvers"]
            if ipaddress.ip_address(address).version == version
        )
        if resolvers:
            # Docker DNATs its embedded resolver to a high loopback port before
            # filter/output. Permit only the original resolver:53 tuple, not
            # arbitrary loopback ports or replies to a different connection.
            lines.append(
                f"{prefix} meta l4proto {{ tcp, udp }} ct direction original "
                f"ct original {family} daddr {{ {resolvers} }} "
                "ct original proto-dst 53 accept"
            )
    # The server and evals share the locked-down uid. Replies on the executor's
    # bound control socket must reach internal API clients; this never permits
    # a new outbound connection to those clients. Health probes can reach only
    # the executor's own loopback TCP control socket.
    lines.append(f"{prefix} ct direction reply tcp sport 8060 accept")
    lines.append(
        f"{prefix} ct direction original meta l4proto tcp "
        "ip daddr 127.0.0.1 th dport 8060 accept"
    )
    lines.append(f"{prefix} fib daddr type local reject")
    for version, family in ((4, "ip"), (6, "ip6")):
        networks = ", ".join(str(n) for n in rules["networks"] if n.version == version)
        lines.append(f"{prefix} {family} daddr {{ {networks} }} reject")
    # IPv6 permits only global unicast, excluding transition mechanisms above.
    lines.append(f"{prefix} meta nfproto ipv6 ip6 daddr != 2000::/3 reject")
    ports = ", ".join(str(p) for p in rules["ports"])
    if ports:
        for transport in ("tcp", "udp"):
            lines.append(f"{prefix} {transport} dport {{ {ports} }} accept")
    lines.append(f"{prefix} reject")
    return "\n".join(lines) + "\n"


def prepare_egress(ports):
    """Must succeed before spawning either execution mode. No permissive cache."""
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise RuntimeError(
            "Egress policy needs a privileged Linux network-policy installer"
        )
    nft = shutil.which("nft")
    if not nft:
        raise RuntimeError("Egress policy needs nftables")
    raw = os.environ.get("CODE_EXECUTOR_INTERNAL_CIDRS", "")
    internal = tuple(part.strip() for part in raw.split(",")) if raw else ()
    rules = policy_rules(ports, internal, resolvers=read_resolvers())
    subprocess.run(
        [nft, "-f", "-"],
        input=nft_script(rules),
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
        close_fds=True,
    )


def validate_runtime_status(status):
    try:
        valid = (
            all(
                [int(x) for x in status[key].split()] == [EVAL_UID] * 4
                for key in ("Uid", "Gid")
            )
            and all(
                int(status[key], 16) == 0
                for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")
            )
            and int(status["NoNewPrivs"]) == 1
        )
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise RuntimeError("Executor identity or capabilities are unsafe")


def verify_egress(ports):
    """Verify init's immutable witness and unread change monitor, without caps."""
    from egress_runtime import verify_policy

    raw = os.environ.get("CODE_EXECUTOR_INTERNAL_CIDRS", "")
    internal = tuple(part.strip() for part in raw.split(",")) if raw else ()
    verify_policy(nft_script(policy_rules(ports, internal, resolvers=read_resolvers())))


def socket_filter(machine):
    """Classic BPF: native ABI only, IP sockets only, no alternate socket APIs."""
    if machine in ("x86_64", "amd64"):
        arch, socket_nr, pair_nr = 0xC000003E, 41, 53
    elif machine in ("aarch64", "arm64"):
        arch, socket_nr, pair_nr = 0xC00000B7, 198, 199
    else:
        raise RuntimeError("Unsupported seccomp architecture")
    # (opcode, jt, jf, k); seccomp_data: nr=0, arch=4, args[0]=16.
    # KILL_PROCESS on a foreign ABI also rejects x86 compat socketcall.
    return [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, arch),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        (0x45, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x80000000),  # no x32
        (0x15, 0, 1, 425),
        (0x06, 0, 0, 0x50000 | errno.EPERM),  # io_uring_setup
        (0x15, 0, 1, pair_nr),
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x15, 0, 4, socket_nr),
        (0x20, 0, 0, 16),
        (0x15, 2, 0, 2),  # AF_INET
        (0x15, 1, 0, 10),  # AF_INET6
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x06, 0, 0, 0x7FFF0000),
    ]


def restrict_child():
    """Apply before exec, after dropping identity. Failure prevents user code."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux seccomp is required")
    if os.getuid() != EVAL_UID or os.geteuid() != EVAL_UID or os.getgid() != EVAL_UID:
        raise RuntimeError("Eval must run under its dedicated unprivileged identity")
    if any(group != EVAL_UID for group in os.getgroups()):
        raise RuntimeError("Unexpected supplementary group")

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint32),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    instructions = socket_filter(platform.machine())
    filters = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
    program = Program(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    # no_new_privs persists through exec; setuid binaries/file capabilities cannot
    # regain root or CAP_NET_ADMIN and remove/evade the uid-scoped firewall.
    if (
        libc.prctl(
            ctypes.c_int(38),
            ctypes.c_ulong(1),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
        != 0
    ):
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
    if (
        libc.prctl(
            ctypes.c_int(22),
            ctypes.c_ulong(2),
            ctypes.byref(program),
            ctypes.c_ulong(0),
            ctypes.c_ulong(0),
        )
        != 0
    ):
        raise OSError(ctypes.get_errno(), "PR_SET_SECCOMP")


if __name__ == "__main__":
    try:
        restrict_child()
        os.execv(sys.argv[1], sys.argv[1:])
    except Exception:
        # No launcher diagnostics or environment are exposed to submitted code.
        print(
            '{"status":"error","error_code":"EGRESS_POLICY_UNAVAILABLE","data":"Cannot apply eval network policy"}'
        )
        sys.exit(1)
