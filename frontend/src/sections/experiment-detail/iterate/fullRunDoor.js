// HO S7: the gate door. When a draft version's publish gate says rows were
// left out (`left_out`) or only a quick test ran (`quick`), the banner and
// the dock offer `Run every row on v3.1`. It opens the dock on the run the
// fix started from (it ran every row) with Rerun scope All, the fix's own
// changes and the same version name, so the dock's Run starts a full run of
// that version (I1 save_as naming an existing draft version reuses it).
// Opening the dock on the left-out run itself would rerun only its own rows
// (iterateModel.scopeSets: `All` = every row that run ran). Pure.
import { iterateHref } from "src/sections/common/Analysis/analysisLinks";
import { collectChanges } from "./iterateModel";

/** The gate states the door answers (publishGate.js). */
export const FULL_RUN_STATES = Object.freeze(["left_out", "quick"]);

/** `Run every row on v3.1`. */
export const fullRunLabel = (versionName) => `Run every row on ${versionName}`;

const setIn = (obj, key, patch) => ({
  ...obj,
  [key]: { ...(obj[key] ?? {}), ...patch },
});

/**
 * The dock draft fields a run's I1 `changes[]` set: the inverse of
 * iterateModel.collectChanges for every axis it writes. Null when a change
 * has no draft field (the door then stays away: it could not replay it).
 * @param {object[]} changes
 * @returns {object|null}
 */
export function draftOfChanges(changes) {
  let d = {};
  for (const c of changes ?? []) {
    const field = c?.field ?? (c?.node ? "prompt" : null);
    if (c?.axis === "agent" && c.node && field === "prompt") {
      d.node = d.node ?? c.node;
      d = setIn(d, "prompts", { [c.node]: c.value });
    } else if (c?.axis === "agent" && field === "max_iterations") {
      d.maxIterations = c.value;
    } else if (c?.axis === "model" && field === "id") {
      d = setIn(d, "model", { id: c.value });
    } else if (c?.axis === "params" && field === "temperature") {
      d = setIn(d, "model", { temperature: c.value });
    } else if (c?.axis === "params" && field === "max_tokens") {
      d = setIn(d, "model", { maxTokens: c.value });
    } else if (c?.axis === "tools" && field === "mode" && c.node) {
      d = setIn(d, "tools", {
        modes: { ...(d.tools?.modes ?? {}), [c.node]: c.value },
      });
    } else if (c?.axis === "tools" && field === "max_tool_turns") {
      d = setIn(d, "tools", { maxToolTurns: c.value });
    } else {
      return null;
    }
  }
  return d;
}

/**
 * The door for a gate, or null.
 * @param {{gate: {state: string}|null, cand: object|null,
 *   versionName: string|null, path: string}} args
 *   `cand` = the run the gate judges (R1 `include=config,lineage`: its
 *   `parent_run_id` and `changes`); `path` = the page's path and query.
 * @returns {{label: string, href: string, baseId: string,
 *   draft: object}|null}  `draft` = the patch for the base run's dock draft
 *   (the fix's edits, saved under the same version name, nothing ran yet).
 */
export function fullRunDoorOf({ gate, cand, versionName, path }) {
  if (!gate || !FULL_RUN_STATES.includes(gate.state)) return null;
  const baseId = cand?.parent_run_id ?? null;
  if (!baseId || !versionName) return null;
  const edits = draftOfChanges(cand.changes);
  if (!edits || !Object.keys(edits).length) return null;
  const href = iterateHref(path ?? "", { runId: baseId, scope: "all" });
  const node = edits.node
    ? `${href.includes("?") ? "&" : "?"}node=${encodeURIComponent(edits.node)}`
    : "";
  return {
    label: fullRunLabel(versionName),
    href: `${href}${node}`,
    baseId,
    // Every edit field is set (null = the base run's value), so edits left
    // in the base run's draft by another try never ride along.
    draft: {
      prompts: edits.prompts ?? {},
      messages: null,
      model: edits.model ?? null,
      tools: edits.tools ?? null,
      harness: null,
      maxIterations: edits.maxIterations ?? null,
      saveName: versionName,
      ran: null,
    },
  };
}

/**
 * The run the dock's last fix started, as the door reads it, when the runs
 * list does not carry it (a quick test is off R1): its parent is the dock's
 * run and its changes are the edits that ran (`ran.edits`, the dock's
 * editsKey snapshot), through the one changes builder. Null when unknown.
 * @param {{run_id: string, config?: object}|null} parent  the dock's run
 * @param {{edits?: string}|null} ran  the dock draft's `ran`
 */
export function ranCandOf(parent, ran) {
  if (!parent?.run_id || !ran?.edits) return null;
  let edits;
  try {
    edits = JSON.parse(ran.edits);
  } catch {
    return null;
  }
  return {
    run_id: ran.runId ?? null,
    parent_run_id: parent.run_id,
    changes: collectChanges(parent.config, edits),
  };
}
