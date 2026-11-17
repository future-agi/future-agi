import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { fetchGraphDataset } from "src/api/agent-playground/agent-playground";
import { useGlobalVariablesDrawerStore } from "../store";

/**
 * Cell writes that put a row's values into the playground's variables
 * dataset: one per dataset column whose name has a prefill value.
 * When exactly one variable has no value of its own and the row has exactly
 * one input (`soleInput`), that variable takes it, as the experiment's
 * one-click mapping does.
 * @param {{columns?: Array<{id:string,name:string}>, rows?: Array}} dataset
 * @param {Record<string,string>} prefill variable name -> value
 * @param {{soleInput?: string}} [opts]
 * @returns {Array<{cellId:string, name:string, value:string}>}
 */
export function cellWritesFor(dataset, prefill, { soleInput } = {}) {
  if (!dataset?.columns?.length || !prefill) return [];
  const row = dataset.rows?.[0];
  if (!row) return [];
  const unmatched = dataset.columns.filter(
    (c) => typeof prefill[c.name] !== "string",
  );
  const fallback =
    unmatched.length === 1 && typeof soleInput === "string"
      ? { [unmatched[0].name]: soleInput }
      : {};
  return dataset.columns
    .map((col) => {
      const value = prefill[col.name] ?? fallback[col.name];
      if (typeof value !== "string") return null;
      const cell = row.cells?.find(
        (c) => (c.columnId || c.column_id) === col.id,
      );
      if (!cell || cell.value === value) return null;
      return { cellId: cell.id, name: col.name, value };
    })
    .filter(Boolean);
}

/**
 * Opened from an experiment row ("Open in Agent Playground"): the row's
 * variables arrive as router state `prefillVariables`; they are written into
 * the playground's variables dataset once and the variables drawer opens.
 */
export default function usePrefillRowVariables(graphId, versionId) {
  const location = useLocation();
  const queryClient = useQueryClient();
  const prefill = location.state?.prefillVariables;
  const soleInput = location.state?.prefillInput;
  const done = useRef(null);

  useEffect(() => {
    if (!graphId || !versionId || !prefill) return;
    const key = `${graphId}:${versionId}:${location.key}`;
    if (done.current === key) return;
    done.current = key;
    let cancelled = false;
    (async () => {
      const dataset = await fetchGraphDataset(graphId, versionId);
      const writes = cellWritesFor(dataset, prefill, { soleInput });
      await Promise.all(
        writes.map((w) =>
          axios.put(endpoints.agentPlayground.datasetCell(graphId, w.cellId), {
            value: w.value,
          }),
        ),
      );
      if (cancelled) return;
      await queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graph-dataset", graphId],
      });
      useGlobalVariablesDrawerStore.getState().setOpen(true);
    })().catch(() => {
      done.current = null;
    });
    return () => {
      cancelled = true;
    };
  }, [graphId, versionId, prefill, soleInput, location.key, queryClient]);
}
