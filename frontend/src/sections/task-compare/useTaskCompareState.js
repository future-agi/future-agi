// Side by side · URL ↔ state. G0's readTaskCompareParams / taskCompareHref
// own the §C4 keys; this hook adds the workspace's own view keys (`dock`,
// `inspect`, `align`, `width`, `prompts`, `wrap`, `wf`, `alltrials`) and the
// rail rule (absent `rail` → the Matrix's `show` when we came from it, else
// Differ; picking All writes `rail=all` explicitly). Every write is
// `replace`. One view: no depth; old `depth=` / `detail` / `dock=answers`
// links are mapped once (oneViewParams.legacyParamPatch) and dropped.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import {
  readTaskCompareParams,
  serializeTrials,
  taskCompareHref,
} from "src/sections/common/Analysis/analysisLinks";
import { railFromMatrixShow } from "./workspaceModel";
import { inspectOf, legacyParamPatch } from "./oneViewParams";

export const DOCK_TABS = Object.freeze(["notes"]);
export const ALIGN_MODES = Object.freeze(["milestone", "index", "none"]);

/** The `from` URL's search params (only same-app paths). */
export function fromParams(from) {
  if (!from || !String(from).startsWith("/")) return null;
  const i = from.indexOf("?");
  return new URLSearchParams(i >= 0 ? from.slice(i + 1) : "");
}

/** Absent `rail` → the Matrix `show` from `from`, else `differ`. */
export function resolveRail(sp, from) {
  if (sp.has("rail")) {
    const v = sp.get("rail");
    if (v === "worse" || /^failed:[\w:-]+$/.test(v ?? "")) return v;
    return ["all", "differ", "flaky", "regressed", "unreviewed"].includes(v)
      ? v
      : "differ";
  }
  const fp = fromParams(from);
  if (fp?.has("show")) return railFromMatrixShow(fp.get("show"));
  return "differ";
}

const EXTRA_KEYS = [
  "focus",
  "depth",
  "gsel",
  "gdiff",
  "turn",
  "dock",
  "align",
  "width",
  "prompts",
  "wrap",
  "wf",
  "alltrials",
  "inspect",
  // IT-R5 G4 P4: the pair a link with more runs than lanes opens on.
  "cand",
];

export default function useTaskCompareState() {
  const [sp, setSp] = useSearchParams();
  const location = useLocation();
  const params = useMemo(() => readTaskCompareParams(sp), [sp]);
  // An old link asked for every step open (`depth=steps`): the workspace
  // expands them once the alignment is known, then clears this.
  const [legacyExpandAll, setLegacyExpandAll] = useState(false);
  const mapped = useRef(false);

  const rail = resolveRail(sp, params.from);
  const dockRaw = sp.get("dock");
  const view = {
    dock: DOCK_TABS.includes(dockRaw) ? dockRaw : null,
    inspect: inspectOf(sp.get("inspect")),
    align: ALIGN_MODES.includes(sp.get("align"))
      ? sp.get("align")
      : "milestone",
    width: sp.get("width") === "time" ? "time" : "equal",
    prompts: sp.get("prompts") === "1",
    // One line per step by default; `Wrap long lines` (View) wraps.
    wrap: sp.get("wrap") === "1",
    wf: sp.get("wf") === "norm" ? "norm" : "abs",
    allTrials: sp.get("alltrials") === "1",
  };

  /** Patch the URL (replace). Keys: §C4 names + the view keys above. */
  const set = useCallback(
    (patch) => {
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev);
          const put = (k, v) => {
            if (v === null || v === undefined || v === "" || v === false)
              next.delete(k);
            else next.set(k, v === true ? "1" : String(v));
          };
          Object.entries(patch).forEach(([k, v]) => {
            switch (k) {
              case "runIds":
                put("runs", (v ?? []).join(","));
                break;
              case "baselineId":
                put("baseline", v);
                break;
              case "rowId":
                put("row", v);
                break;
              case "trials":
                put("trial", serializeTrials(v ?? {}));
                break;
              case "level":
                put("level", v === "milestones" ? null : v);
                break;
              case "rtab":
                put("rtab", v === "waterfall" ? null : v);
                break;
              case "fmt":
                put("fmt", v === "abs" ? null : v);
                break;
              case "rail":
                put("rail", v);
                break;
              case "align":
                put("align", v === "milestone" ? null : v);
                break;
              case "width":
                put("width", v === "equal" ? null : v);
                break;
              case "wrap":
                put("wrap", v ? "1" : null);
                break;
              case "wf":
                put("wf", v === "abs" ? null : v);
                break;
              case "allTrials":
                put("alltrials", v);
                break;
              default:
                put(k, v);
            }
          });
          return next;
        },
        { replace: true, state: location.state },
      );
    },
    [setSp, location.state],
  );

  // Old links (`depth=`, `detail`, `dock=answers|checks`, `rtab=`): map
  // once, then drop the old params (replace).
  useEffect(() => {
    if (mapped.current) return;
    mapped.current = true;
    const m = legacyParamPatch(sp);
    if (!m) return;
    set(m.patch);
    if (m.expandAll) setLegacyExpandAll(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /** Absolute link to the current view (Copy link), with every view key. */
  const linkFor = useCallback(
    (datasetId, extra = {}) => {
      const p = { ...params, ...extra };
      const href = taskCompareHref({
        datasetId,
        runIds: p.runIds,
        baselineId: p.baselineId,
        rowId: p.rowId,
        trials: p.trials,
        rail,
        step: p.step,
        from: p.from,
        level: p.level,
        diff: p.diff,
        blind: p.blind,
        fmt: p.fmt,
      });
      const url = new URL(href, window.location.origin);
      if (rail === "all" || rail === "worse" || rail.startsWith("failed:"))
        url.searchParams.set("rail", rail);
      EXTRA_KEYS.forEach((k) => {
        if (sp.has(k)) url.searchParams.set(k, sp.get(k));
      });
      return url.toString();
    },
    [params, rail, sp],
  );

  return {
    params,
    rail,
    view,
    set,
    linkFor,
    sp,
    legacyExpandAll,
    clearLegacyExpandAll: () => setLegacyExpandAll(false),
  };
}
