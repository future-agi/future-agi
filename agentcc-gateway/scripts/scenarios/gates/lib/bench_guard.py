"""Benchmark guard for the gate checks.

Compares interleaved go-bench runs of the PIN and of this tree:

  * ns/op: one-sided Mann-Whitney U (normal approximation with tie
    correction, as benchstat uses). A benchmark fails when this tree's
    median is more than MAX_SLOWDOWN slower AND the slowdown is significant
    (p < ALPHA). On a loaded host the noise is far above 1 percent, so a
    literal "within 1%" cannot be measured; this is the guard instead.
  * allocs/op: this tree's median may not exceed the pin median by 1 or
    more. A consistent extra allocation per request fails; the 234/235
    jitter the pin itself shows does not. An approved allocation budget B
    (--allocs-budget B) lets the median rise by at most B.

  * unpinned benchmarks (--unpinned NAME[,NAME...]): a benchmark the head
    has and the pin does not (one the head added) is printed with the head's
    figures and decides nothing, but only when it is named here. Once the pin
    has it, it is judged like any other. A benchmark the pin has and the head
    lacks, or an unnamed one the pin lacks, is still bad input.

Usage: python3 -I bench_guard.py [--allocs-budget B] [--unpinned NAMES] <pin.txt> <head.txt>
Names are given without the -GOMAXPROCS suffix (BenchmarkChatCompletionOwnKey).
Exit status: 0 pass, 1 fail, 2 bad input (a judged benchmark with too few
samples, a benchmark the head lacks, or nothing to judge).
Standard library only.
"""

import math
import re
import sys
from collections import defaultdict

ALPHA = 0.05
MAX_SLOWDOWN = 0.03
MIN_SAMPLES = 10

NAME = re.compile(r"^(Benchmark\S+)")
RESULT = re.compile(r"\d+\s+([\d.]+) ns/op(?:\s+\d+ B/op\s+(\d+) allocs/op)?")


def parse(path):
    """Go prints the benchmark name, then slog lines, then the result."""
    ns, allocs = defaultdict(list), defaultdict(list)
    name = None
    with open(path) as fh:
        for raw in fh:
            m = NAME.match(raw)
            if m:
                name = m.group(1)
            r = RESULT.search(raw)
            if r and name:
                ns[name].append(float(r.group(1)))
                if r.group(2):
                    allocs[name].append(int(r.group(2)))
    return ns, allocs


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def p_greater(pin, head):
    """One-sided p-value for 'head is stochastically greater than pin'."""
    pooled = sorted([(v, 0) for v in pin] + [(v, 1) for v in head])
    ranks = [0.0] * len(pooled)
    ties = 0.0
    i = 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        t = j - i + 1
        ties += t**3 - t
        i = j + 1
    n1, n2 = len(pin), len(head)
    r2 = sum(r for r, (_, group) in zip(ranks, pooled) if group == 1)
    u2 = r2 - n2 * (n2 + 1) / 2
    n = n1 + n2
    sigma = math.sqrt(n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1))))
    if sigma == 0:
        return 1.0
    z = (u2 - n1 * n2 / 2 - 0.5) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2))


def base_name(name):
    """BenchmarkChatCompletionOwnKey-4 -> BenchmarkChatCompletionOwnKey (Go's -GOMAXPROCS suffix)."""
    return re.sub(r"-\d+$", "", name)


def main(pin_path, head_path, allocs_budget=0, unpinned=()):
    pin_ns, pin_allocs = parse(pin_path)
    head_ns, head_allocs = parse(head_path)
    names = sorted(set(pin_ns) | set(head_ns))
    if not names:
        print("bench_guard: no benchmark results parsed")
        return 2
    status = 0
    judged = 0
    for name in names:
        a, b = pin_ns.get(name, []), head_ns.get(name, [])
        if not a and b and base_name(name) in unpinned:
            hb = head_allocs.get(name, [])
            allocs = f"; allocs/op head median {median(hb)} [{min(hb)}-{max(hb)}]" if hb else ""
            print(
                f"{name}: unpinned (the pin has no samples of it): ns/op head median {median(b):.0f} "
                f"[{min(b):.0f}-{max(b):.0f}] n={len(b)}{allocs} -> not judged"
            )
            continue
        judged += 1
        if len(a) < MIN_SAMPLES or len(b) < MIN_SAMPLES:
            print(f"bench_guard: {name}: too few samples (pin {len(a)}, head {len(b)}; need {MIN_SAMPLES})")
            return 2
        slowdown = median(b) / median(a) - 1
        p = p_greater(a, b)
        ns_fail = slowdown > MAX_SLOWDOWN and p < ALPHA
        print(
            f"{name}: ns/op pin median {median(a):.0f} [{min(a):.0f}-{max(a):.0f}] n={len(a)}; "
            f"head median {median(b):.0f} [{min(b):.0f}-{max(b):.0f}] n={len(b)}; "
            f"delta {slowdown * 100:+.1f}% one-sided p={p:.3f} -> {'FAIL' if ns_fail else 'ok'}"
        )
        pa, pb = pin_allocs.get(name, []), head_allocs.get(name, [])
        allocs_fail = False
        if pa and pb:
            delta = median(pb) - median(pa)
            allocs_fail = delta >= 1 + allocs_budget
            budget = f" (budget +{allocs_budget})" if allocs_budget else ""
            print(
                f"  allocs/op pin median {median(pa)} [{min(pa)}-{max(pa)}]; "
                f"head median {median(pb)} [{min(pb)}-{max(pb)}]; delta {delta:+}{budget} -> {'FAIL' if allocs_fail else 'ok'}"
            )
        else:
            print("  allocs/op missing (run with -test.benchmem)")
            return 2
        if ns_fail or allocs_fail:
            status = 1
    if not judged:
        print("bench_guard: no benchmark with samples on both sides to judge")
        return 2
    print("bench_guard:", "FAIL" if status else "PASS")
    return status


def cli(args):
    budget, unpinned, paths = 0, (), []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--allocs-budget", "--unpinned"):
            if i + 1 >= len(args):
                print(__doc__)
                return 2
            value = args[i + 1]
            if arg == "--allocs-budget":
                if not value.isdigit():
                    print(__doc__)
                    return 2
                budget = int(value)
            else:
                unpinned = tuple(n.strip() for n in value.split(",") if n.strip())
            i += 2
        elif arg.startswith("--"):
            print(__doc__)
            return 2
        else:
            paths.append(arg)
            i += 1
    if len(paths) != 2:
        print(__doc__)
        return 2
    return main(paths[0], paths[1], budget, unpinned)


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
