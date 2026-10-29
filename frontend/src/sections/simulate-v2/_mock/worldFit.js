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
export const worldFitOf = (template, agent, binding = null, scenarios = []) => {
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
    return missing.some((m) => text.includes(m.toLowerCase()) || words(m).every((w) => text.includes(w)));
  });
  const channelOk = channel === worldChannel;
  return {
    rows,
    extra,
    live,
    channel: { ok: channelOk, agent: channel, world: worldChannel },
    missing,
    unmeasured,
    mapped: rows.filter((r) => r.kind === "mapped"),
    total: scenarios.length,
    verdict: channelOk && !missing.length ? "ready" : "partial",
    binding: Object.fromEntries(rows.map((r) => [r.world, r.agent])),
  };
};
