// The one-shot Iterate URL (R2-SPEC §0.4): `iterate=<run>&scope=…&rows=…
// &change=<preset>&cause=…`, written by every door through
// analysisLinks.iterateHref. The mounted dock reads it once, opens on that
// run with that scope and change, and drops the params (replace) so a
// reload or Back does not reopen it.
import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { readR2Params } from "src/sections/common/Analysis/analysisLinks";
import { useIterateStore } from "./iterateStore";

// iterate r2 G3 (P3): `back` (the page the door was on, e.g. the trace
// compare) is one-shot too; `cand` / `baseline` stay (the header keeps
// the pair the user was looking at).
const ONE_SHOT = [
  "iterate",
  "scope",
  "rows",
  "change",
  "cause",
  "node",
  "back",
  "iterate_from",
];

/** A same-app path from `back=` (decoded once more if still encoded), or null. */
export function backOf(raw) {
  if (!raw) return null;
  let v = String(raw);
  if (/^%2F/i.test(v)) {
    try {
      v = decodeURIComponent(v);
    } catch {
      return null;
    }
  }
  return v.startsWith("/") && !v.startsWith("//") ? v : null;
}

/** The entry a search string names, or null. Pure; exported for tests. */
export function entryFromSearch(search) {
  const p = readR2Params(search);
  if (!p.iterate) return null;
  return {
    runId: p.iterate,
    scope: p.scope ?? "all",
    rows: p.rows ?? [],
    change: p.change ?? null,
    cause: p.cause ?? null,
    // R2D-15: the node to open on (the first wrong step's node).
    node: new URLSearchParams(search).get("node") || null,
    back: backOf(new URLSearchParams(search).get("back")),
  };
}

/** `search` without the one-shot params (`?…` or ``). Pure. */
export function withoutOneShot(search) {
  const params = new URLSearchParams(search);
  ONE_SHOT.forEach((k) => params.delete(k));
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** Reads and clears the one-shot params; opens the dock on them. */
export default function useIterateParams() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const open = useIterateStore((s) => s.open);
  const seen = useRef(null);
  useEffect(() => {
    const entry = entryFromSearch(search);
    if (!entry) return;
    const key = `${pathname}${search}`;
    if (seen.current === key) return;
    seen.current = key;
    open({ runId: entry.runId, entry });
    navigate({ pathname, search: withoutOneShot(search) }, { replace: true });
  }, [pathname, search, navigate, open]);
}
