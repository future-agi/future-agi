// W6-2 (A3, `review:compare-table`): what an agent graph version uses, read
// from its version detail (`GET /agent-playground/graphs/:id/versions/:v/`,
// the same read the estimate makes): the models of its llm nodes in graph
// order, its tool and retriever nodes, and its node count. Pure; Review's
// Model / Tools cells (runUses/agent.js) and the estimator's agent inputs
// (integration H2) read the same answer.
const modelName = (m) =>
  typeof m === "string" ? m : m?.name ?? m?.value ?? null;

const kindOf = (n) => n?.node_type ?? n?.type ?? null;

/** `{models, tools, nodes}` of a version detail, or null without nodes. */
export function agentUsesOf(versionDetail) {
  const nodes = versionDetail?.nodes ?? [];
  if (!nodes.length) return null;
  const models = [
    ...new Set(
      nodes
        .map((n) => modelName(n?.prompt_template?.model ?? n?.model))
        .filter(Boolean),
    ),
  ];
  const tools = nodes
    .filter((n) => ["tool", "retriever"].includes(kindOf(n)))
    .map((n) => n?.name ?? n?.label ?? n?.id)
    .filter(Boolean);
  return { models, tools, nodes: nodes.length };
}

/** The cards with each agent card's `agentUses` (same objects otherwise). */
export function withAgentUses(cards = [], detailsByVersion = new Map()) {
  return (cards ?? []).map((c) => {
    if (c?.targetType !== "agent" || !c?.agentVersion) return c;
    const uses = agentUsesOf(detailsByVersion.get(c.agentVersion));
    return uses ? { ...c, agentUses: uses } : c;
  });
}

/** Review's cells: `Model gpt-4o-mini + gpt-4o`, `Tools lookup_order, …`. */
export function agentUsesCells(uses) {
  if (!uses) return {};
  return {
    ...(uses.models.length ? { model: uses.models.join(" + ") } : {}),
    tools: uses.tools.length ? uses.tools.join(", ") : "—",
  };
}
