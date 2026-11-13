/*
  Which fix a Ready to run row offers, so a fix is made where it shows (the
  run plan) whenever it's small enough: one connection field, pointing the
  tools at the test world, the environment's release checks, or a rebuild for this environment's own agent. Anything else is a change
  on the agent, named for what it changes. A Partial row is a stated limit
  of how the agent is hosted — there's nothing to fix, so no fix.
*/
import { inlineRepairOf } from "../_mock/connectionCheck";

/** "field" | "tools" | "release" | "rebuild" | "link" | null */
export const fixKindOf = (row, kit) => {
  if (!row || ["partial", "unavailable"].includes(row.status)) return null;
  const inline = kit?.spec ? inlineRepairOf(kit.spec, row.fixId) : null;
  if (inline) return inline.kind;
  if (row.repair === "release" && kit?.runReleaseChecks) return "release";
  if (row.repair === "rebuild" && kit?.rebuild) return "rebuild";
  return "link";
};

/** What the row's fix button says, when the fix is made elsewhere. */
export const fixLabelOf = (row) => row?.fixLabel || null;

/* "Add a, b and 3 more to the agent" → "Add 5 tools to the agent": the names
   are on the row's hover. */
export const shortFixLabel = (label) => {
  const m = /^Add (.+?) and (\d+) more (to .+)$/.exec(label || "");
  if (!m) return label;
  const n = m[1].split(",").length + Number(m[2]);
  return `Add ${n} tools ${m[3]}`;
};

/** The field an inline field fix edits, prefilled with the check's suggestion. */
export const fieldFixOf = (row, kit) => {
  const inline = inlineRepairOf(kit?.spec, row?.fixId);
  if (inline?.kind !== "field") return null;
  const suggested = Object.values(row?.suggestion?.connection || {})[0];
  return { label: inline.label, value: suggested ?? inline.value ?? "" };
};
