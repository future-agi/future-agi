"""Canonical JSON and the v1 payload builders (DESIGN.md section 14; B8-EVENT-CONTRACT.md section 7)."""

from __future__ import annotations

import json
import re
from decimal import Decimal

THIN_COMMIT_CHARS = 200

FULL_COMMIT_CHARS = 1000

NIL_UUID = "00000000-0000-0000-0000-000000000000"

TEST_DEP = "dep-0000000000000000"

SECRET_SHAPES = [
    re.compile(p) for p in (r"sk-[A-Za-z0-9_-]{8,}", r"whsec_[A-Za-z0-9+/=]{8,}")
]

# One pattern, so masking is leftmost-first and non-overlapping in every language (re.sub, String.replace with /g).
SECRET_SHAPE_ANY = re.compile("|".join(r.pattern for r in SECRET_SHAPES))

REDACTED = "[redacted]"


def es_number(v) -> str:
    """ECMAScript Number::toString of the IEEE-754 double nearest ``v``: the text JSON.stringify emits.

    Python's repr gives the same shortest round-trip digits; only the layout differs (``1e-05`` vs ``0.00001``,
    ``1.152921504606847e+18`` vs ``1152921504606847000``), so the digits are re-laid out per ECMA-262 section 6.1.6.1.20.
    """
    x = float(v)
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("NaN or Infinity is not JSON")
    if x == 0:
        return "0"
    if x < 0:
        return "-" + es_number(-x)
    sign, digits, exp = Decimal(repr(x)).as_tuple()
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
    mant = ds if k == 1 else ds[0] + "." + ds[1:]
    return f"{mant}e{'+' if e >= 0 else '-'}{abs(e)}"


def _write(v, out: list[str]) -> None:
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
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, dict):
        out.append("{")
        for i, (k, x) in enumerate(v.items()):
            if i:
                out.append(",")
            out.append(json.dumps(str(k), ensure_ascii=False) + ":")
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
        raise TypeError(type(v).__name__)


def canonical(obj) -> bytes:
    """The one body serialization: compact, keys in insertion order, raw UTF-8, numbers per es_number."""
    out: list[str] = []
    _write(obj, out)
    return "".join(out).encode("utf-8")


def envelope(
    *, event_id, type_, timestamp, organization_id, workspace_id, data, test=False
) -> dict:
    env = {
        "id": event_id,
        "type": type_,
        "api_version": "v1",
        "timestamp": timestamp,
        "organization_id": organization_id,
        "workspace_id": workspace_id,
    }
    if test:
        env["test"] = True
    env["data"] = data
    return env


def person(p, include_names: bool):
    if p is None:
        return None
    return {"id": p["id"], "name": p.get("name")} if include_names else {"id": p["id"]}


def scrub(text: str) -> str:
    """Mask every secret shape in free text with REDACTED (DESIGN.md section 14.1, P2)."""
    return SECRET_SHAPE_ANY.sub(REDACTED, text)


def _cut(text, mode):
    """Scrub first, then cut by code points: cutting first could leave a secret's head (``sk-a``) too short to match."""
    if text is None:
        return None, False
    text = scrub(text)
    limit = THIN_COMMIT_CHARS if mode == "thin" else FULL_COMMIT_CHARS
    return (text[:limit], True) if len(text) > limit else (text, False)


_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


def es_key_order(obj: dict) -> dict:
    """The order JSON.parse gives an object's keys (ECMA-262 OrdinaryOwnPropertyKeys): array-index keys (canonical
    decimal integers below 2^32 - 1) ascending by value, then the other keys in received order. Customer-chosen keys
    (``webhook_context``) are written in this order, so a JavaScript port that parses the notice signs the same bytes."""
    idx = sorted(
        (k for k in obj if _ARRAY_INDEX.fullmatch(k) and int(k) < 2**32 - 1), key=int
    )
    return {
        **{k: obj[k] for k in idx},
        **{k: v for k, v in obj.items() if k not in idx},
    }


def has_secret_shape(value) -> bool:
    if isinstance(value, str):
        return any(r.search(value) for r in SECRET_SHAPES)
    if isinstance(value, dict):
        return any(has_secret_shape(k) or has_secret_shape(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_secret_shape(v) for v in value)
    return False


def _content(src):
    out = {}
    if "messages" in src:
        out["messages"] = [
            {"role": m["role"], "text": m["text"]} for m in src["messages"]
        ]
    if "template" in src:
        out["template"] = src["template"]
    out["variables"] = list(src.get("variables", []))
    out["model"] = src.get("model")
    return out


def _add_content(data, src, variant):
    if variant["payload_mode"] != "full" or src is None:
        return
    c = _content(src)
    if has_secret_shape(c):
        data["content_omitted"] = "secret_shape"
    else:
        data["content"] = c


def _label(lbl, test):
    if test:
        return {"id": NIL_UUID, "name": "[Test] " + lbl["name"], "type": lbl["type"]}
    return {"id": lbl["id"], "name": lbl["name"], "type": lbl["type"]}


def build_prompt_version_created(inp: dict, variant: dict) -> dict:
    test = inp.get("test", False)
    v = inp["version"]
    msg, cut = _cut(v.get("commit_message"), variant["payload_mode"])
    version = {
        "id": NIL_UUID if test else v["id"],
        "version": v["version"],
        "commit_message": msg,
    }
    if cut:
        version["commit_message_truncated"] = True
    version.update(
        is_default=v["is_default"],
        labels=[_label(lbl, test) for lbl in v.get("labels", [])],
        created_at=v["created_at"],
        created_by=person(v.get("created_by"), variant["include_people_names"]),
    )
    prev = inp.get("previous_version")
    data = {
        "prompt": {
            "id": NIL_UUID if test else inp["prompt"]["id"],
            "name": ("[Test] " if test else "") + inp["prompt"]["name"],
        },
        "version": version,
        "previous_version": None
        if prev is None
        else {"id": NIL_UUID if test else prev["id"], "version": prev["version"]},
        "links": dict(inp["links"]),
    }
    _add_content(data, inp.get("content"), variant)
    return envelope(
        event_id=inp["event_id"],
        type_="prompt.version.created",
        timestamp=v["created_at"],
        organization_id=inp["organization_id"],
        workspace_id=inp["workspace_id"],
        data=data,
        test=test,
    )


def build_prompt_label_changed(inp: dict, variant: dict) -> dict:
    test = inp.get("test", False)
    frm, to = inp.get("from_version"), inp.get("to_version")
    action = "assigned" if frm is None else ("removed" if to is None else "moved")
    if inp.get("cause", "label_change") != "label_change" and action != "removed":
        raise ValueError("replaced and version_deleted are removals")

    def vref(x, with_msg):
        if x is None:
            return None
        r = {"id": NIL_UUID if test else x["id"], "version": x["version"]}
        if with_msg:
            msg, cut = _cut(x.get("commit_message"), variant["payload_mode"])
            r["commit_message"] = msg
            if cut:
                r["commit_message_truncated"] = True
        return r

    data = {
        "action": action,
        "cause": inp.get("cause", "label_change"),
        "prompt": {
            "id": NIL_UUID if test else inp["prompt"]["id"],
            "name": ("[Test] " if test else "") + inp["prompt"]["name"],
        },
        "label": _label(inp["label"], test),
        "revision": inp["revision"],
        "from_version": vref(frm, False),
        "to_version": vref(to, True),
        "changed_at": inp["changed_at"],
        "changed_by": person(inp.get("changed_by"), variant["include_people_names"]),
        "links": dict(inp["links"]),
    }
    _add_content(data, inp.get("content"), variant)
    return envelope(
        event_id=inp["event_id"],
        type_="prompt.label.changed",
        timestamp=inp["changed_at"],
        organization_id=inp["organization_id"],
        workspace_id=inp["workspace_id"],
        data=data,
        test=test,
    )
