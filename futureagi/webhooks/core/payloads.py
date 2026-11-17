"""The canonical body serializer, the v1 envelope and the prompt event builders (DESIGN.md sections 7.5 and 14;
conformance ``payloads.json``, ``build`` cases and ``rules.serialization``).

One serializer for every body: compact UTF-8 JSON, keys in builder order, raw non-ASCII, numbers exactly as
ECMAScript's ``Number::toString`` writes them (what ``JSON.stringify`` emits), so a port in another language signs
the same bytes.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from webhooks.core import prompt_content

API_VERSION = "v1"
MAX_BODY_BYTES = 64 * 1024
THIN_COMMIT_CHARS = 200
FULL_COMMIT_CHARS = 1000
NIL_UUID = "00000000-0000-0000-0000-000000000000"
TEST_DEPLOYMENT_ID = "dep-0000000000000000"
TEST_PREFIX = "[Test] "
REDACTED = "[redacted]"
SECRET_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{8,}|whsec_[A-Za-z0-9+/=]{8,}")
"""The shapes the vectors pin. FA's scrubber may know more; it is injected where free text is stored (section 8.1)."""

PAYLOAD_MODES = ("thin", "full")


# ---- canonical JSON ---------------------------------------------------------------------------------------------


def es_number(value: float | int) -> str:
    """ECMA-262 Number::toString of the double nearest ``value``. Python's repr already gives the shortest
    round-trip digits; only the layout differs (``1e-05`` vs ``0.00001``), so the digits are laid out again."""
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("NaN or Infinity is not JSON")
    if x == 0:
        return "0"
    if x < 0:
        return "-" + es_number(-x)
    _sign, digits, exp = Decimal(repr(x)).as_tuple()
    ds = "".join(map(str, digits)).rstrip("0")
    exp += len(digits) - len(ds)
    ds = ds.lstrip("0") or "0"
    k = len(ds)
    n = exp + k
    if k <= n <= 21:
        return ds + "0" * (n - k)
    if 0 < n <= 21:
        return ds[:n] + "." + ds[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + ds
    e = n - 1
    mantissa = ds if k == 1 else ds[0] + "." + ds[1:]
    return f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"


def _string(s: str) -> str:
    # json.dumps gives RFC 8259 escapes with lower-case hex and raw non-ASCII; a lone surrogate cannot be encoded
    # below, which is the "every string is valid Unicode" rule.
    return json.dumps(s, ensure_ascii=False)


def _write(v: Any, out: list[str]) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int) and abs(v) < 2**53:
        out.append(str(v))
    elif isinstance(v, int | float):
        out.append(es_number(v))
    elif isinstance(v, str):
        out.append(_string(v))
    elif isinstance(v, Mapping):
        out.append("{")
        for i, (k, x) in enumerate(v.items()):
            if i:
                out.append(",")
            out.append(_string(str(k)))
            out.append(":")
            _write(x, out)
        out.append("}")
    elif isinstance(v, list | tuple):
        out.append("[")
        for i, x in enumerate(v):
            if i:
                out.append(",")
            _write(x, out)
        out.append("]")
    else:
        raise TypeError(f"not JSON: {type(v).__name__}")


def canonical(obj: Any) -> bytes:
    """The exact body bytes. Raises on NaN, Infinity or a lone surrogate (UnicodeEncodeError)."""
    out: list[str] = []
    _write(obj, out)
    return "".join(out).encode("utf-8")


_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


def es_key_order(obj: Mapping[str, Any]) -> dict:
    """The key order ``JSON.parse`` gives (ECMA-262 OrdinaryOwnPropertyKeys): array-index keys ascending, then the rest
    as received. Used for customer-chosen keys (``run.context``)."""
    idx = sorted(
        (k for k in obj if _ARRAY_INDEX.fullmatch(k) and int(k) < 2**32 - 1), key=int
    )
    rest = [k for k in obj if k not in set(idx)]
    return {k: obj[k] for k in idx + rest}


# ---- envelope and shared pieces ---------------------------------------------------------------------------------


def envelope(
    *,
    event_id: str,
    type_: str,
    timestamp: str,
    organization_id: str,
    workspace_id: str | None,
    data: dict,
    test: bool = False,
) -> dict:
    """Section 14.1: id, type, api_version, timestamp, organization_id, workspace_id, [test], data."""
    env = {
        "id": event_id,
        "type": type_,
        "api_version": API_VERSION,
        "timestamp": timestamp,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
    }
    if test:
        env["test"] = True
    env["data"] = data
    return env


def person(p: Mapping[str, Any] | None, include_names: bool) -> dict | None:
    """``{id, name}`` (name = the display name or null; never an email), ``{id}`` without names, null for no one."""
    if p is None:
        return None
    return {"id": p["id"], "name": p.get("name")} if include_names else {"id": p["id"]}


def has_secret_shape(value: Any) -> bool:
    if isinstance(value, str):
        return SECRET_SHAPE.search(value) is not None
    if isinstance(value, Mapping):
        return any(has_secret_shape(k) or has_secret_shape(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return any(has_secret_shape(v) for v in value)
    return False


def scrub(text: str) -> str:
    """Every secret shape, leftmost first and non-overlapping, becomes ``[redacted]``."""
    return SECRET_SHAPE.sub(REDACTED, text)


def commit_message(text: str | None, mode: str) -> tuple[str | None, bool]:
    """Scrub, then cut by code points: 200 thin, 1,000 full, no ellipsis. Returns (text, truncated)."""
    if text is None:
        return None, False
    text = scrub(text)
    limit = THIN_COMMIT_CHARS if mode == "thin" else FULL_COMMIT_CHARS
    return (text[:limit], True) if len(text) > limit else (text, False)


def _variant(variant: Mapping[str, Any]) -> tuple[str, bool]:
    mode = variant["payload_mode"]
    if mode not in PAYLOAD_MODES:
        raise ValueError(f"unknown payload mode {mode!r}")
    return mode, bool(variant["include_people_names"])


def _add_content(data: dict, src: Mapping[str, Any] | None, mode: str) -> None:
    if mode == "full" and src is not None:
        prompt_content.add(data, src, has_secret_shape)


def fit_content(env: dict) -> dict:
    return prompt_content.fit_body(env, lambda e: len(canonical(e)), MAX_BODY_BYTES)


def _label(lbl: Mapping[str, Any], test: bool) -> dict:
    if test:
        return {"id": NIL_UUID, "name": TEST_PREFIX + lbl["name"], "type": lbl["type"]}
    return {"id": lbl["id"], "name": lbl["name"], "type": lbl["type"]}


def _prompt(inp: Mapping[str, Any], test: bool) -> dict:
    return {
        "id": NIL_UUID if test else inp["prompt"]["id"],
        "name": (TEST_PREFIX if test else "") + inp["prompt"]["name"],
    }


# ---- prompt events (XP-06 supplies the inputs) ------------------------------------------------------------------


def build_prompt_version_created(
    inp: Mapping[str, Any], variant: Mapping[str, Any]
) -> dict:
    """Section 14.2; ``timestamp`` = ``version.created_at``."""
    mode, names = _variant(variant)
    test = bool(inp.get("test", False))
    v = inp["version"]
    msg, cut = commit_message(v.get("commit_message"), mode)
    version: dict = {
        "id": NIL_UUID if test else v["id"],
        "version": v["version"],
        "commit_message": msg,
    }
    if cut:
        version["commit_message_truncated"] = True
    version["is_default"] = v["is_default"]
    version["labels"] = [_label(lbl, test) for lbl in v.get("labels", [])]
    version["created_at"] = v["created_at"]
    version["created_by"] = person(v.get("created_by"), names)
    prev = inp.get("previous_version")
    data = {
        "prompt": _prompt(inp, test),
        "version": version,
        "previous_version": None
        if prev is None
        else {"id": NIL_UUID if test else prev["id"], "version": prev["version"]},
        "links": dict(inp["links"]),
    }
    _add_content(data, inp.get("content"), mode)
    return fit_content(
        envelope(
            event_id=inp["event_id"],
            type_="prompt.version.created",
            timestamp=v["created_at"],
            organization_id=inp["organization_id"],
            workspace_id=inp["workspace_id"],
            data=data,
            test=test,
        )
    )


LABEL_CAUSES = ("label_change", "replaced", "version_deleted")


def build_prompt_label_changed(
    inp: Mapping[str, Any], variant: Mapping[str, Any]
) -> dict:
    """Section 14.3. ``action`` is assigned (no from), removed (no to) or moved; ``replaced`` and
    ``version_deleted`` are removals. ``timestamp`` = ``changed_at``."""
    mode, names = _variant(variant)
    test = bool(inp.get("test", False))
    frm, to = inp.get("from_version"), inp.get("to_version")
    if frm is None and to is None:
        raise ValueError("a label change needs a from or a to version")
    action = "assigned" if frm is None else ("removed" if to is None else "moved")
    cause = inp.get("cause", "label_change")
    if cause not in LABEL_CAUSES:
        raise ValueError(f"unknown cause {cause!r}")
    if cause != "label_change" and action != "removed":
        raise ValueError("replaced and version_deleted are removals")

    def ref(x: Mapping[str, Any] | None, with_message: bool) -> dict | None:
        if x is None:
            return None
        r: dict = {"id": NIL_UUID if test else x["id"], "version": x["version"]}
        if with_message:
            msg, cut = commit_message(x.get("commit_message"), mode)
            r["commit_message"] = msg
            if cut:
                r["commit_message_truncated"] = True
        return r

    data = {
        "action": action,
        "cause": cause,
        "prompt": _prompt(inp, test),
        "label": _label(inp["label"], test),
        "revision": inp["revision"],
        "from_version": ref(frm, False),
        "to_version": ref(to, True),
        "changed_at": inp["changed_at"],
        "changed_by": person(inp.get("changed_by"), names),
        "links": dict(inp["links"]),
    }
    _add_content(data, inp.get("content"), mode)
    return fit_content(
        envelope(
            event_id=inp["event_id"],
            type_="prompt.label.changed",
            timestamp=inp["changed_at"],
            organization_id=inp["organization_id"],
            workspace_id=inp["workspace_id"],
            data=data,
            test=test,
        )
    )


def render(event: Mapping[str, Any]) -> bytes:
    """The stored body of one variant; refuses a body over the 64 KB cap."""
    body = canonical(event)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError(f"body is {len(body)} bytes; the cap is {MAX_BODY_BYTES}")
    return body


BUILDERS = {
    "prompt.version.created": build_prompt_version_created,
    "prompt.label.changed": build_prompt_label_changed,
}
"""Event types whose body depends on the variant (payload mode, people names). Other types (``deployment.run.*``,
built once at B8 intake) are published with their final ``data``."""


def variant_body(
    *,
    event_id: str,
    type_: str,
    timestamp: str,
    organization_id: str,
    workspace_id: str | None,
    data: Mapping[str, Any],
    builder_input: Mapping[str, Any] | None,
    variant: Mapping[str, Any],
    test: bool = False,
) -> bytes:
    """One variant's exact bytes (section 4.6: one body per event, mode, api_version and names)."""
    if builder_input is not None:
        if type_ not in BUILDERS:
            raise ValueError(f"no variant builder for {type_!r}")
        inp = {
            **builder_input,
            "event_id": event_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "test": test,
        }
        return render(BUILDERS[type_](inp, variant))
    return render(
        envelope(
            event_id=event_id,
            type_=type_,
            timestamp=timestamp,
            organization_id=organization_id,
            workspace_id=workspace_id,
            data=dict(data),
            test=test,
        )
    )
