"""Full payload mode's prompt content (DESIGN.md section 14.1, P3): the allowlist, the secret-shape rule and the
64 KB cut. Pure functions; the body size and the secret test are passed in by ``payloads``.

- **Allowlist:** message roles and text, or the template text; variable names; the model name. Nothing else.
- **Secret shape:** if any allowlisted value matches, the content is left out whole (``content_omitted:
  "secret_shape"``).
- **64 KB:** when the body is over the cap, every text (each message's text, the template) is cut to the same number
  of Unicode code points, the largest number for which the body fits, and ``content_truncated: true`` follows
  ``content``. The receiver fetches the whole text from ``links.api``. A port computes the same cut: the largest
  such number is unique.
- **Too large even cut to nothing** (the variable names or the model name alone exceed the cap; they are never cut):
  the content is left out, ``content_omitted: "too_large"`` in its place, so the endpoint still gets the event.
  (An additive value of ``content_omitted`` within v1, handed to the WH-0 owner with its conformance case.)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def allowlisted(src: Mapping[str, Any]) -> dict:
    out: dict = {}
    if "messages" in src:
        out["messages"] = [
            {"role": m["role"], "text": m["text"]} for m in src["messages"]
        ]
    if "template" in src:
        out["template"] = src["template"]
    out["variables"] = list(src.get("variables", []))
    out["model"] = src.get("model")
    return out


def add(
    data: dict, src: Mapping[str, Any], has_secret_shape: Callable[[Any], bool]
) -> None:
    """Append ``content`` (or ``content_omitted``) to ``data``, after every other key."""
    c = allowlisted(src)
    if has_secret_shape(c):
        data["content_omitted"] = "secret_shape"
    else:
        data["content"] = c


def cut(c: Mapping[str, Any], cap: int) -> dict:
    """``c`` with every text cut to its first ``cap`` code points."""
    out = dict(c)
    if "messages" in c:
        out["messages"] = [{**m, "text": m["text"][:cap]} for m in c["messages"]]
    if "template" in c:
        out["template"] = c["template"][:cap]
    return out


def fit_body(env: dict, size: Callable[[dict], int], limit: int) -> dict:
    """``env`` itself when its body fits in ``limit`` bytes or it has no content; else the envelope with the content
    cut as the module docstring says, or left out (``too_large``) when even texts cut to nothing do not fit."""
    if "content" not in env["data"] or size(env) <= limit:
        return env
    full = env["data"]["content"]
    texts = [m["text"] for m in full.get("messages", ())]
    if "template" in full:
        texts.append(full["template"])

    def with_cap(cap: int | None) -> dict:
        data: dict = {}
        for k, v in env["data"].items():
            if k != "content":
                data[k] = v
            elif cap is None:
                data["content_omitted"] = "too_large"
            else:
                data[k] = cut(full, cap)
                data["content_truncated"] = True
        return {**env, "data": data}

    # Invariant: a cap of ``lo`` fits (``-1`` stands for "none found yet"), a cap of ``hi`` does not.
    lo, hi = -1, max((len(t) for t in texts), default=0)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if size(with_cap(mid)) <= limit:
            lo = mid
        else:
            hi = mid
    return with_cap(lo if lo >= 0 else None)
