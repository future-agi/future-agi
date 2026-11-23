import { goalLabel } from "../../_mock/scenarios";

/**
 * Use-case-based grouping.
 *
 * Kind buckets (Tool use / Rule enforcement / Data traps / …) group by
 * how the scenario was *derived*, which is a builder concept. A reader
 * scanning 30+ scenarios cares about *what task the agent is being
 * asked to do*: verify identity, look up an order, refuse a refund,
 * handle a saved-card record. Those are the real use cases and they
 * cut across the derivation kinds.
 *
 * The scenario's title carries this cleanly:
 *   Core   → "Routine task using {tool_name}"  → group by the tool
 *   Rule   → the rule text (short, one per rule)
 *   Trap   → "{table_name}: {note}"            → group by the table
 *   Adv    → the template title (Instruction override, Authority claim…)
 *   Edge   → the template title
 *
 * So every group's label is what the scenarios in it are actually
 * doing, not the builder bucket they fell out of.
 */
const humanize = (s = "") =>
  s
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();

export const deriveUseCase = (row) => {
  /*
    The scenario now carries its own use-case sentence — the mock stamps
    `row.useCase` with a spec-line description of what the group tests
    ("Verify a caller's identity before touching account state"). Group
    key is a slug of that sentence so scenarios with matching text land
    in the same group. Legacy scenarios that predate the field fall back
    to the previous id-based derivation.
  */
  if (row?.useCase) {
    const slug = row.useCase
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return { id: slug, label: goalLabel(row.useCase) };
  }
  const id = row?.id || "";
  const title = row?.title || "";
  if (id.includes("-core-")) {
    const m = title.match(/(?:using|needing|landing on)\s+([\w_-]+)/i);
    if (m) return { id: `tool:${m[1].toLowerCase()}`, label: humanize(m[1]) };
    return { id: "tool:other", label: "Tool use" };
  }
  if (id.includes("-rule-")) {
    const short = title.split(/[.:]/)[0].slice(0, 42).trim();
    return {
      id: `rule:${short.toLowerCase()}`,
      label: short || "Rule enforcement",
    };
  }
  if (id.includes("-trap-")) {
    const table = title.split(":")[0].trim();
    if (table)
      return {
        id: `trap:${table.toLowerCase()}`,
        label: `${humanize(table)} data`,
      };
    return { id: "trap:other", label: "Data traps" };
  }
  if (id.includes("-adversarial-"))
    return { id: `adv:${title.toLowerCase()}`, label: title || "Adversarial" };
  if (id.includes("-edge-"))
    return { id: `edge:${title.toLowerCase()}`, label: title || "Edge case" };
  return { id: "other", label: "Other" };
};
