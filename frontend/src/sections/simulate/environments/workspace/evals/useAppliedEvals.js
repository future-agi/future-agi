import { useMemo } from "react";
import { resolveEval } from "src/api/simulate-environments/_fixtures/evalCatalog";
import { EVALS_COPY } from "./evals.constants";

/**
 * Applied evals — the add/remove rules and reading over `envState.evals`.
 *
 * Kept in one hook so the Evals step (and any later Overview card) share the
 * same normalisation: stored entries can be bare ids or configured objects,
 * both resolve to one catalogue-shaped row.
 */
// Stable empty reference so the memos below don't re-run every render when the
// slice is absent.
const EMPTY = [];

export function useAppliedEvals(envState, patch) {
  const applied = envState?.evals || EMPTY;

  const appliedEvals = useMemo(() => applied.map(resolveEval).filter(Boolean), [applied]);

  const appliedIds = useMemo(() => new Set(appliedEvals.map((e) => e.id)), [appliedEvals]);

  const add = (entries) => {
    const fresh = entries.filter((e) => !appliedIds.has(e.id));
    if (fresh.length) patch({ evals: [...applied, ...fresh] });
  };

  const remove = (id) =>
    patch({ evals: applied.filter((e) => (typeof e === "string" ? e : e.id) !== id) });

  /**
   * Normalise a configured eval the picker returns into the same shape as a
   * catalogue eval so both render through one row. Not consumed by the Evals
   * step today (the drawer builds its own entries) — kept for parity with the
   * Overview card that also adds evals.
   */
  const onEvalAdded = (config) => {
    const id = config.templateId || config.id || `eval-${config.name}`;
    const mapping = config.mapping || {};
    add([
      {
        id,
        name: config.name || config.evalTemplate?.name || EVALS_COPY.pickerFallbackName,
        blurb: Object.keys(mapping).length
          ? Object.entries(mapping)
              .map(([k, v]) => `${k} → ${v}`)
              .join(" · ")
          : EVALS_COPY.pickerBlurb,
        mapping,
        model: config.model,
        custom: true,
      },
    ]);
  };

  return { applied, appliedEvals, appliedIds, add, remove, onEvalAdded };
}
