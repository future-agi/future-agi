/*
  Which fix a Ready to run row offers, so a fix is made where it shows (the
  run plan) whenever it's small enough: one connection field, pointing the
  tools at the test world, the environment's release checks, or a rebuild for this environment's own agent. A URL hard-coded in the
  agent's code is fixed in a new agent version. Anything else is a change
  on the agent, named for what it changes. A Partial row is a stated limit
  of how the agent is hosted — there's nothing to fix, so no fix.
*/
import { inlineRepairOf } from "../_mock/connectionCheck";

/* A production URL hard-coded in the agent's own code (#17): the fix is a
   new agent version, never the builder chat. The last check's row carries
   only the check's wording, so it's matched on that too. */
export const fixedInCode = (row) =>
  row?.fixIn === "code" ||
  (row?.fixId === "tools" && /hard-coded/.test(row?.detail || ""));

/** What a fix in the agent's code says, in place of the builder's fix. */
export const CODE_FIX = {
  action: "Upload a new agent version",
  fix: "Read the URL from the environment in the agent's code, then upload a new agent version.",
  note: "the fix is in the agent's code",
};

/** "field" | "tools" | "release" | "rebuild" | "code" | "link" | null */
export const fixKindOf = (row, kit) => {
  if (!row || ["partial", "unavailable"].includes(row.status)) return null;
  if (fixedInCode(row)) return "code";
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
