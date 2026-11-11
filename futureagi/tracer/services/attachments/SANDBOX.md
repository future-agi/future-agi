# Attachment media sandbox

Every byte of attachment media that the server parses, segments, splits or
summarises is handled by `media_worker.py`, started by `sandbox.py` as a
separate process: `python -I -S -B media_worker.py <op> <in-fd> <out-fd> <limit>`.

## Why not bubblewrap

The first runner used `bwrap --unshare-all`. Creating a network namespace
needs privileges that GCP VMs (Ubuntu 24.04 AppArmor user-namespace
restrictions) and default Docker / Kubernetes `RuntimeDefault` containers do
not grant, so it failed with `RTM_NEWADDR EPERM`. It failed closed, but no
media could be validated in the self-host or worker images.

## What the worker does instead

Before it reads the first untrusted byte, the worker:

1. imports only standard-library modules, with `-I -S`: no site-packages,
   no Django, no settings, no application code;
2. sets its own limits: 512 MiB address space, 30 s CPU, output file size
   capped at the exact expected output, 16 descriptors, no core dumps;
3. sets `PR_SET_NO_NEW_PRIVS` and installs a seccomp-bpf **allowlist**.

From then on it can only read and write descriptors it already holds and
manage its own memory. Anything else (`openat`, `socket`, `connect`, `execve`,
`clone`/`fork`, `ptrace`, `kill`, x32 syscalls, a foreign architecture) kills it
with `SIGSYS`. The supported architectures are x86_64 and aarch64; anything
else exits as unavailable.

The parent passes one read-only input descriptor (opened with `O_NOFOLLOW`)
and at most one output descriptor. The child gets an empty environment, no
stdin, no stderr, `cwd=/`, a new session and a 60 s wall-clock timeout. It
does not inherit credentials, because the environment is empty, it cannot
open files, and it receives no sockets. Exit code 2 or `SIGSYS` means the input
was rejected. Any other failure, including a missing kernel feature, means
`validation_unavailable`. There is never an in-process fallback.

## Proving it is active

`sandbox.self_test()` runs three negative controls (`deny-socket`,
`deny-open`, `deny-exec`). Each must die with `SIGSYS`. Readiness
(`tracer.services.attachments.readiness`) runs it, so a runtime whose
seccomp or `no_new_privs` support is missing fails readiness instead of
silently running unconfined.

## Supported runtimes

- Linux kernel 3.17 or later with `CONFIG_SECCOMP_FILTER`.
- Docker's default seccomp profile and Kubernetes `RuntimeDefault`: both allow
  `prctl(PR_SET_SECCOMP)` and nested filters. No added capability, privileged
  mode, AppArmor change or user namespace is needed.
- gVisor (`runsc`), which implements seccomp-bpf.
- A non-Linux development host is not supported: the worker exits as unavailable.

## What this does not cover

- The parent still holds application credentials. The boundary is the child
  process, which is why the child never receives a socket or file path.
- A seccomp allowlist bounds what a compromised parser can do. It does not
  make an unsafe parser safe. The worker parses only canonical PCM WAV, using
  fixed-size header checks and bounded reads.
