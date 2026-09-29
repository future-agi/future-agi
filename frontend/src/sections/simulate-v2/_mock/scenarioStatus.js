/**
 * One status per scenario — the answer to "can I trust this row?".
 *
 * A scenario used to carry its state as scattered marks: a red triangle, a
 * "Broken" chip, an amber shield, a "New" tag. Each meant something, none of
 * them lined up in a column, and none could be filtered on together. This
 * reduces them to one ordered answer, worst first, with the one line that
 * explains it.
 *
 *   broken        the world changed and the scenario no longer stages
 *   quarantined   it can't be proved (no outcome, no checks …)
 *   needs-env     added on a later environment version; not part of the pinned one
 *   no-tool       it needs a tool this world can't answer
 *   stale         proved on an older world (or edited since) — re-prove
 *   proved        fine, on the pinned version
 *
 * "Release blocker" is not a status — it is how much a failure matters — so
 * it travels separately (`critical`).
 */
import { admissionOf } from "./coverage";
import { proofStatus } from "./proofs";
import { versionNumber } from "./versions";

export const STATUS_ORDER = ["checking", "broken", "quarantined", "needs-env", "no-tool", "stale", "proved"];

export const STATUS_META = {
  checking: { label: "Checking…", tone: null },
  broken: { label: "Broken", tone: "#DC2626" },
  quarantined: { label: "Quarantined", tone: "#CA8A04" },
  "needs-env": { label: "Needs newer env", tone: null },
  "no-tool": { label: "Tool not in world", tone: "#CA8A04" },
  stale: { label: "Needs re-proof", tone: "#CA8A04" },
  proved: { label: "Proved", tone: null },
};

/**
 * `ctx` = { env, envState, envVersion, answers (Set of tool names the pinned
 * world answers), buildMode }.
 */
export const scenarioStatus = (row, ctx) => {
  const { env, envState, envVersion, answers, buildMode } = ctx;
  /* Written by the build and still going through its checks — each scenario
     is checked as it's written, and kept only once it passes. */
  if (row?.checking) {
    return {
      id: "checking",
      ...STATUS_META.checking,
      detail: "Just written — being checked that the world holds what it presumes, that it can be solved, and that doing nothing fails it.",
    };
  }
  if (row?.provedBroke && (!row.brokeAgainst || !envVersion || row.brokeAgainst === envVersion)) {
    return { id: "broken", ...STATUS_META.broken, detail: "It no longer stages on this world since the environment changed. Re-prove or edit it." };
  }
  const admission = admissionOf(row);
  if (!admission.admitted) {
    return { id: "quarantined", ...STATUS_META.quarantined, detail: admission.reason };
  }
  if (row?.addedInEnv && envVersion && versionNumber(row.addedInEnv) > versionNumber(envVersion)) {
    return {
      id: "needs-env",
      ...STATUS_META["needs-env"],
      label: `Needs env ${row.addedInEnv}`,
      detail: row.newTool
        ? `Added when environment ${row.addedInEnv} learned ${row.newTool}. Runs on ${envVersion} leave it out.`
        : `Added on environment ${row.addedInEnv}, after ${envVersion}. Runs on ${envVersion} leave it out.`,
    };
  }
  const missing = answers ? (row?.requiredTools || []).filter((t) => !answers.has(t)) : [];
  if (missing.length) {
    return {
      id: "no-tool",
      ...STATUS_META["no-tool"],
      detail: `Needs ${missing.join(", ")}, which environment ${envVersion} can't answer — it comes back not measured until the environment is rebuilt.`,
    };
  }
  if (!buildMode) {
    const proof = proofStatus(row, env, envState);
    if (proof.stale) {
      return {
        id: "stale",
        ...STATUS_META.stale,
        label: proof.edited ? "Edited · re-prove" : STATUS_META.stale.label,
        detail: proof.edited
          ? "Edited after it was proved — the proof is of the old version."
          : `Proved on environment ${proof.proved}; the world has changed since.`,
      };
    }
    return { id: "proved", ...STATUS_META.proved, label: `Proved · ${proof.proved}`, detail: `Staged, solvable and not vacuous on environment ${proof.proved}.` };
  }
  /* One the checks sent back once: its first draft passed while the agent
     did nothing, so it was rewritten and checked again before it was kept. */
  if (row?.rewritten) {
    return {
      id: "proved",
      ...STATUS_META.proved,
      label: "Proved · rewritten",
      detail: "The first draft passed while the agent did nothing, so it was rewritten and checked again. Staged, solvable and not vacuous now.",
    };
  }
  return { id: "proved", ...STATUS_META.proved, detail: "Staged, solvable and not vacuous." };
};

/* Something to fix before the results can be trusted. A scenario added on a
   later environment version is correctly left out of this one, and one still
   being checked is mid-build — neither is waiting on the user. */
const NO_ACTION = new Set(["proved", "needs-env", "checking"]);
export const needsAttention = (status) => !NO_ACTION.has(status.id);
