/* Fixed file-capability launcher. No root, SETUID, SETGID or SETPCAP needed.
 * OCI supplies only NET_ADMIN in the bounding set; this file's cap_net_admin=ep
 * enables it for the non-root init. Python installs/verifies the policy, clears
 * ALL caps and sets no_new_privs before exec. Arguments are never forwarded.
 */
#define _GNU_SOURCE
#include <linux/capability.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static void refuse(void) {
    fputs("EGRESS_POLICY_UNAVAILABLE: executor capability init refused\n", stderr);
    _exit(1);
}

static char *setting(const char *key, const char *fallback) {
    const char *value = getenv(key);
    if (!value || !*value) value = fallback;
    if (strlen(value) > 8192) refuse();
    char *out = NULL;
    if (asprintf(&out, "%s=%s", key, value) < 0) refuse();
    return out;
}

int main(void) {
    struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct caps[2] = {{0}, {0}};
    unsigned int net_admin = 1U << CAP_NET_ADMIN;
    if (getuid() != 65534 || geteuid() != 65534 || getgid() != 65534 || getegid() != 65534)
        refuse();
    if (syscall(SYS_capget, &header, caps) != 0) refuse();
    if (caps[0].effective != net_admin || caps[0].permitted != net_admin ||
        (caps[0].inheritable & ~net_admin) || caps[1].effective || caps[1].permitted || caps[1].inheritable)
        refuse();
    caps[0].inheritable = net_admin;
    if (syscall(SYS_capset, &header, caps) != 0 ||
        prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_RAISE, CAP_NET_ADMIN, 0, 0) != 0)
        refuse();
    char *env[] = {
        "PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8",
        "PYTHONDONTWRITEBYTECODE=1",
        setting("CODE_EXECUTOR_EGRESS_PORTS", "80,443"),
        setting("CODE_EXECUTOR_INTERNAL_CIDRS", ""),
        setting("CODE_EXECUTOR_MAX_PROCESSES", "64"), NULL,
    };
    char *argv[] = {"python3", "-I", "-B", "/sandbox/executor_init.py", NULL};
    execve("/usr/local/bin/python3", argv, env);
    refuse();
}
