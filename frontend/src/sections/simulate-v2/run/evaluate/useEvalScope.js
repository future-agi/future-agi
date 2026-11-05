import { useMemo, useState } from "react";
import {
  missingOf,
  scopeNameOf,
  scopeTrialsOf,
} from "../../_mock/evalEvidence";

/*
  The trials an evaluation will read, and what each is missing — worked out
  once, so the composer's button, its counts and the list of gaps all say
  the same thing before anything is spent.
*/
export function useEvalScope({ ctx, filter, selectedIds, fields, initial }) {
  const hasFilter = !!filter?.taskIds?.length;
  const hasSelection = !!selectedIds?.length;
  const [scope, setScope] = useState(() => {
    const s = initial?.scope || "all";
    if (s === "filter" && !hasFilter) return "all";
    if (s === "selected" && !hasSelection) return "all";
    return s;
  });
  const [runIds, setRunIds] = useState(() =>
    (initial?.evidence?.runs || [])
      .map((r) => r.run)
      .filter(
        (id) => id !== ctx.runId && (ctx.others || []).some((o) => o.id === id),
      ),
  );
  const shared = { ctx, filter, selectedIds };
  const trials = useMemo(
    () => scopeTrialsOf({ ...shared, scope, runIds }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ctx, filter, selectedIds, scope, runIds],
  );
  const missing = useMemo(
    () => missingOf(trials, fields, ctx),
    [trials, fields, ctx],
  );
  /* How many trials each choice would read, for the picker's own rows. */
  const countOf = (value) =>
    scopeTrialsOf({ ...shared, scope: value, runIds }).length;
  return {
    scope,
    setScope,
    runIds,
    setRunIds,
    trials,
    missing,
    readable: trials.length - missing.length,
    scopeLabel: scopeNameOf({ scope, filter, selectedIds }),
    hasFilter,
    hasSelection,
    countOf,
  };
}
