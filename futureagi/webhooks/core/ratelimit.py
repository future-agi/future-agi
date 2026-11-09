"""The per-endpoint send rate, shared by every dispatcher loop and replica (DESIGN.md section 7.8; R6, WT-20).

A generic cell rate algorithm on the endpoint's state row: ``tat`` is the theoretical arrival time of the next send.
A claim reserves a send start for each delivery it leases, ``start = max(tat, now)``, allowed while
``start - now <= burst``; then ``tat = start + 1 / rate``. The sender waits until its start before it connects.

So consecutive starts to one endpoint are at least ``1 / rate`` apart whichever loop or replica leased them (the
state row is read and written under its row lock), and any 1 s window holds at most ``rate + 1`` starts: the "10/s,
at most 11 in any second" of WT-20. ``burst`` is how far ahead a pass may reserve; it is the busy poll interval, so a
loop that passes every ``burst`` keeps a 10/s endpoint at 10/s. A 429 halves the rate for 5 minutes (section 7.2).
"""

from __future__ import annotations

import math

BURST_MS = 250
"""How far ahead of now a claim may reserve a send start; the dispatcher passes at least this often while busy."""
MIN_RATE = 0.5


def effective_rate(rate_per_s: float, halved: bool) -> float:
    rate = float(rate_per_s)
    return max(MIN_RATE, rate / 2) if halved else rate


def interval_ms(rate_per_s: float) -> float:
    return 1000.0 / max(MIN_RATE, float(rate_per_s))


def room(
    tat_ms: float | None, now_ms: float, rate_per_s: float, burst_ms: float = BURST_MS
) -> int:
    """How many starts a claim may reserve now."""
    base = now_ms if tat_ms is None else max(tat_ms, now_ms)
    ahead = now_ms + burst_ms - base
    if ahead < 0:
        return 0
    return int(math.floor(ahead / interval_ms(rate_per_s))) + 1


def reserve(
    tat_ms: float | None, now_ms: float, rate_per_s: float, burst_ms: float = BURST_MS
) -> tuple[float | None, float | None, int]:
    """``(start_ms, new_tat_ms, wait_ms)``: a reserved start and the new ``tat``; or ``(None, tat, wait_ms)`` when
    no start is free, ``wait_ms`` being when the next one could be reserved."""
    base = now_ms if tat_ms is None else max(tat_ms, now_ms)
    if base - now_ms > burst_ms:
        return None, tat_ms, max(1, int(math.ceil(base - now_ms - burst_ms)))
    return base, base + interval_ms(rate_per_s), 0


def max_in_window(starts_ms: list[float], window_ms: float = 1000.0) -> int:
    """The most starts inside any half-open window of ``window_ms`` (what the receiver measures in WT-20)."""
    s = sorted(starts_ms)
    best, j = 0, 0
    for i, t in enumerate(s):
        while s[j] <= t - window_ms:
            j += 1
        best = max(best, i - j + 1)
    return best
