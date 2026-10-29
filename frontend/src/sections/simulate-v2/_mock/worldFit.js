/*
  Fitting your agent to a ready world (roadmap catalogue.md "Review fit"):
  the world's suite stays fixed; what changes is how its tools bind to
  yours. The report lists matched tools, mappings to review (cancel_visit →
  cancel_appointment, identity and refusal semantics as well as fields),
  world tools your agent has no equivalent for — their cases are unmeasured,
  never silently dropped — and your agent's own tools the world doesn't
  model, which act live on a live agent.

  Ready: every world tool is matched or mapped, so the supported suite runs.
  Partial: the unmeasured cases are listed, with ways to repair.
*/
import { agentDefinitionOf, agentIsVoice } from "./agentRegistry";
import { replicationOf } from "./replication";
import { platformPreviewOf } from "./platformPreview";
import { assistantsFor } from "./connectAgent";
import { hashSeed } from "./runStream";

/*
  A mapping is more than a name match (roadmap platforms.md "Fit"): the same
  record (identity), the same units and formats, the same refusals and
  errors. A difference keeps the row at Needs input until someone reviews
  it — and stays visible after, never hidden by a clean schema conversion.
*/
const semanticChecksOf = (worldTool, agentTool) => {
  const args = worldTool?.args || [];
  const wn = words(worldTool?.name || "");
  const an = words(agentTool || "");
  const h = hashSeed(`${worldTool?.name}:${agentTool}`);
  const checks = [
    { id: "fields", label: "Fields", ok: true, line: args.length ? `${args.length} of ${args.length} fields line up (${args.join(", ")})` : "No fields to line up" },
  ];
  const idArg = args.find((a) => /_id$/.test(a));
  if (idArg) {
    const same = wn.slice(1).some((w) => an.slice(1).includes(w));
    checks.push(
      same
        ? { id: "identity", label: "Identity", ok: true, line: `${idArg} names the same record on both sides` }
        : { id: "identity", label: "Identity", ok: false, line: `${idArg} is fed from ${agentTool}'s own ID — confirm it names the same record`, fix: `Map ${agentTool}'s ID field to ${idArg}` },
    );
  }
  if (args.some((a) => /date|time|slot/.test(a)) && h % 2 === 0)
    checks.push({ id: "units", label: "Units and formats", ok: false, line: `The world takes times as 2026-11-04T09:00-05:00 (ISO with zone); ${agentTool} sends local time like "4 Nov 9am"`, fix: "Convert to ISO time with the caller's zone in the mapping" });
  else if (args.some((a) => /amount|fee|price/.test(a)))
    checks.push({ id: "units", label: "Units and formats", ok: false, line: `The world counts money in cents; ${agentTool} sends dollars`, fix: "Multiply by 100 in the mapping" });
  if (/rule|applying|refus|only if/i.test(worldTool?.desc || ""))
    checks.push({ id: "errors", label: "Refusals and errors", ok: false, line: `The world refuses with a reason the caller must hear (e.g. the late-cancellation fee); ${agentTool} returns a generic error, so a refusal reads like a failure`, fix: "Pass the world's refusal reason through as the tool result" });
  return checks;
};

/* The seeded record types; a table no bound tool reads is out of the agent's reach. */
const tablesOf = (template) => (Array.isArray(template?.seed) ? template.seed : template?.seed?.tables || []);
const singular = (w) => w.replace(/ies$/, "y").replace(/s$/, "");

/* Words that name the same thing, so names that differ only by them map. */
const SAME = {
  visit: "appointment", booking: "appointment", reservation: "appointment", slot: "appointment",
  get: "lookup", find: "lookup", fetch: "lookup", search: "lookup", read: "lookup", list: "lookup",
  remove: "cancel", delete: "cancel", void: "cancel",
  client: "customer", user: "customer", member: "customer", patient: "customer",
  parcel: "order", shipment: "order", purchase: "order",
  create: "make", open: "make", add: "make", book: "make",
  move: "reschedule", change: "update", edit: "update", modify: "update",
};
const words = (name) =>
  String(name).toLowerCase().split(/[^a-z0-9]+/).filter((w) => w && w !== "id").map((w) => SAME[w] || w);

/* How alike two tool names are once synonyms are folded: 1 = the same words. */
const likeness = (a, b) => {
  const wa = words(a);
  const wb = words(b);
  /* The action has to agree: reschedule_appointment is not create_booking. */
  if (wa.length > 1 && wb.length > 1 && wa[0] !== wb[0]) return 0;
  /* …and so does the thing acted on: find_customer is not lookup_order. */
  if (wa.length > 1 && wb.length > 1 && !wa.slice(1).some((w) => wb.slice(1).includes(w))) return 0;
  const x = new Set(wa);
  const y = new Set(wb);
  const both = [...x].filter((w) => y.has(w)).length;
  return both / Math.max(x.size, y.size, 1);
};

/** The agent's tool names, as we read them. */
export const agentToolNamesOf = (agent) =>
  (agentDefinitionOf({ agent }).tools?.items || []).map((t) => t.name).filter(Boolean);

/**
 * The fit of `agent` to `template`, given the tool `binding` so far
 * ({ worldTool: agentTool | null }). Without a binding, each world tool is
 * matched by name or mapped to the most alike agent tool.
 */
export const worldFitOf = (template, agent, binding = null, scenarios = [], reviewed = []) => {
  const agentTools = agentToolNamesOf(agent);
  const worldTools = (template.tools || []).map((t) => t.name);
  const live = replicationOf(agent).proofLabel === "Live";
  const channel = agentIsVoice(agent) ? "voice" : "chat";
  const worldChannel = template.surface === "voice" ? "voice" : "chat";

  const rows = worldTools.map((w) => {
    const chosen = binding && w in binding ? binding[w] : undefined;
    if (chosen !== undefined) {
      return { world: w, agent: chosen, kind: chosen == null ? "missing" : chosen === w ? "matched" : "mapped" };
    }
    if (agentTools.includes(w)) return { world: w, agent: w, kind: "matched" };
    const best = agentTools
      .map((a) => ({ a, s: likeness(a, w) }))
      .sort((p, q) => q.s - p.s)[0];
    return best && best.s >= 0.5
      ? { world: w, agent: best.a, kind: "mapped" }
      : { world: w, agent: null, kind: "missing" };
  });
  const bound = new Set(rows.map((r) => r.agent).filter(Boolean));
  const extra = agentTools.filter((a) => !bound.has(a));
  const missing = rows.filter((r) => r.kind === "missing").map((r) => r.world);
  /* A case is unmeasured when it leans on a world tool nothing is bound to. */
  const unmeasured = scenarios.filter((s) => {
    const text = JSON.stringify(s).toLowerCase();
    /* By name, or by the thing the tool acts on (customer, slot, appointment). */
    return missing.some((m) => text.includes(m.toLowerCase()) || words(m).slice(1).some((w) => text.includes(w)));
  });
  const channelOk = channel === worldChannel;
  /* Mapped rows carry their semantic checks; an unreviewed difference needs input. */
  const done = new Set(reviewed);
  rows.forEach((r) => {
    if (r.kind !== "mapped") return;
    r.checks = semanticChecksOf((template.tools || []).find((t) => t.name === r.world), r.agent);
    r.differs = r.checks.filter((c) => !c.ok);
    r.reviewed = done.has(r.world);
    r.needsInput = r.differs.length > 0 && !r.reviewed;
  });
  /* Seeded records no bound tool reads — the agent can't reach them. */
  const boundWorld = rows.filter((r) => r.agent).map((r) => words(r.world).join(" "));
  const unreachable = tablesOf(template)
    .map((t) => t.name)
    .filter((name) => !boundWorld.some((w) => w.includes(singular(String(name).toLowerCase().split(/[^a-z]+/)[0]))));
  /* A platform agent: the actions its platform performs for real. */
  const v = agent?.values || {};
  const liveActions = v.provider
    ? (platformPreviewOf(v.provider, assistantsFor(v.provider).find((a) => a.id === v.agentId) || { id: v.agentId, name: agent.name })?.tools || []).filter((t) => t.handling === "live")
    : [];
  return {
    rows,
    extra,
    live,
    channel: { ok: channelOk, agent: channel, world: worldChannel },
    missing,
    unmeasured,
    mapped: rows.filter((r) => r.kind === "mapped"),
    total: scenarios.length,
    unreachable,
    liveActions,
    needsInput: rows.filter((r) => r.needsInput),
    /* Ready runs everything; Needs input waits on a reviewed mapping; Partial leaves cases unmeasured. */
    verdict: rows.some((r) => r.needsInput)
      ? "needs_input"
      : channelOk && !missing.length
        ? "ready"
        : "partial",
    binding: Object.fromEntries(rows.map((r) => [r.world, r.agent])),
  };
};
