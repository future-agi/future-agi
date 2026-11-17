import React from "react";
import { useLocation } from "react-router-dom";
import IterateDock from "./IterateDock";
import { useIterateStore } from "./iterateStore";

/** `{datasetId}` of a side-by-side (task compare) path, or null. Pure. */
export function taskComparePath(pathname) {
  const m = String(pathname ?? "").match(
    /^\/dashboard\/develop\/([^/]+)\/compare\/tasks\/?$/,
  );
  return m ? { datasetId: m[1] } : null;
}

/**
 * IT-R5 G3 (P4): `Fix this step` opens the Test-a-fix panel ON the side by
 * side page — the open row and step stay where they are. The experiment
 * and compare pages mount their own dock; this host mounts the same dock
 * on the task compare page while a fix is being edited or run (the dock is
 * a fixed right panel, so the page underneath keeps its layout).
 */
export default function IterateDockHost() {
  const { pathname, search } = useLocation();
  const shown = useIterateStore(
    (s) => (s.opened && s.expanded) || Boolean(s.active),
  );
  const at = taskComparePath(pathname);
  if (!at) return null;
  const sp = new URLSearchParams(search);
  if (!sp.get("iterate") && !shown) return null;
  const runIds = (sp.get("runs") ?? "").split(",").filter(Boolean);
  const baselineId = sp.get("baseline");
  return (
    <IterateDock
      datasetId={at.datasetId}
      runIds={runIds}
      baselineId={baselineId && baselineId !== "none" ? baselineId : null}
    />
  );
}
