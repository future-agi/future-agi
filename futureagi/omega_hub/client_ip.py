"""The one client address the Hub trusts (HUB-04), used by every Hub throttle and by the install counts.

`X-Forwarded-For` is written by the client first and by each proxy after it, so only the hops the
deployment's own proxies appended can be believed. Two settings say which:

- `OMEGA_HUB_CLIENT_IP_HEADER`: a header the edge proxy always overwrites with the client address
  (for example `X-Real-IP`). When it is set and holds an address, it wins.
- `OMEGA_HUB_TRUSTED_PROXY_DEPTH`: how many proxies in front of Django append to
  `X-Forwarded-For`. With depth N the client is the N-th hop from the right. With 0 (the default,
  and the only safe value when nothing is known about the ingress) only `REMOTE_ADDR` counts.

Whatever the settings, a value that is not an IP address is ignored, and a header with fewer hops
than the configured depth falls back to `REMOTE_ADDR`, because a request that did not pass every
trusted proxy carries only client-written hops. The User-Agent is never part of the identity: a
client chooses it freely.
"""

from __future__ import annotations

import ipaddress

from omega_hub import conf


def _address(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.startswith("[") and "]" in value:  # "[2001:db8::1]:443"
        value = value[1:value.index("]")]
    elif value.count(":") == 1:  # "203.0.113.7:51234"
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request) -> str:
    meta = request.META
    remote = _address(meta.get("REMOTE_ADDR")) or ""
    header = conf.client_ip_header()
    if header:
        trusted = _address(meta.get("HTTP_" + header.upper().replace("-", "_")))
        if trusted:
            return trusted
    depth = conf.trusted_proxy_depth()
    if depth <= 0:
        return remote
    hops = [hop.strip() for hop in (meta.get("HTTP_X_FORWARDED_FOR") or "").split(",") if hop.strip()]
    if len(hops) < depth:
        return remote
    return _address(hops[-depth]) or remote
