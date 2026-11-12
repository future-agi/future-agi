// W6 FE slot `runUses/agent` (W6-SCOPE.md §4.2).
// Review's Model / Tools cells of an agent run: the version's llm node
// models ("gpt-4o-mini + gpt-4o") and its tool and retriever nodes, from
// the card's `agentUses` (useCardsWithAgentUses, agentUses.js). A card
// without them changes nothing.
import { agentUsesCells } from "../agentUses";

const slot = {
  usesOf(card, { kind } = {}) {
    if ((kind ?? card?.targetType) !== "agent" || !card?.agentUses) return {};
    return agentUsesCells(card.agentUses);
  },
};

export default slot;
