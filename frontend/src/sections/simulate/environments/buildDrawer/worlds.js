/*
  Prebuilt worlds as Vel's drawer shows them, read from the templates list.
  A world is offered for the agent type it was built for; worlds for agents we
  don't build (coding, simulation) aren't listed.
*/

const TYPE_OF_AGENT = {
  voice_platform: "voice",
  chat_webhook: "chat",
  api_agent: "chat",
  mcp_agent: "chat",
  browser_agent: "computer",
  game_agent: "computer",
  coding_agent: null,
  sim_agent: null,
};
const TYPE_LABEL = { voice: "Voice", chat: "Chat", computer: "Computer use" };

const DOMAIN_LABEL = {
  ecommerce: "E-commerce",
  fintech: "Finance",
  travel: "Travel",
  software: "Software",
  robotics: "Robotics",
  gaming: "Games",
  research: "Research",
  operations: "Operations",
  ml: "Machine learning",
  hardware: "Hardware",
  support: "Support",
  sales: "Sales",
};
const domainLabelOf = (d) =>
  DOMAIN_LABEL[d] || (d ? d.charAt(0).toUpperCase() + d.slice(1) : "Other");

/** The templates as worlds, most used first. */
export function worldsFrom(templates = []) {
  return templates
    .map((t) => {
      const type =
        t.agentType in TYPE_OF_AGENT ? TYPE_OF_AGENT[t.agentType] : "chat";
      if (!type) return null;
      return {
        id: t.id,
        name: t.name,
        tagline: t.tagline,
        type,
        typeLabel: TYPE_LABEL[type],
        domain: t.domain || "other",
        domainLabel: domainLabelOf(t.domain),
        tools: (t.tools || []).length,
        tables: (t.seed?.tables || []).length,
        popularity: t.popularity || 0,
        template: t,
      };
    })
    .filter(Boolean)
    .sort((a, b) => b.popularity - a.popularity);
}

/** Computer use worlds can't be built for yet: listed, not opened. */
export const isSoonWorld = (world) => world.type === "computer";

/** The agents a world is for: voice agents for a voice world, else chat ones. */
export const modalityOfWorld = (world) =>
  world?.type === "voice" ? "voice" : "text";

/** Every world the browser lists, with the given filters. */
export function filterWorlds(
  worlds,
  { query = "", type = "all", domain = "all" },
) {
  const q = query.trim().toLowerCase();
  return worlds.filter(
    (w) =>
      (type === "all" || w.type === type) &&
      (domain === "all" || w.domain === domain) &&
      (!q ||
        `${w.name} ${w.tagline} ${w.domainLabel} ${w.typeLabel}`
          .toLowerCase()
          .includes(q)),
  );
}

export const WORLD_TYPES = [
  { id: "voice", label: "Voice", icon: "solar:phone-calling-rounded-linear" },
  { id: "chat", label: "Chat", icon: "solar:chat-round-linear" },
  {
    id: "computer",
    label: "Computer use",
    icon: "solar:monitor-linear",
    soon: true,
  },
];

export { domainLabelOf };

/** Three up front, a different agent type first where there is one; computer use is never featured. */
export function featuredWorlds(worlds) {
  const open = worlds.filter((w) => w.type !== "computer");
  const seen = new Set();
  const firsts = open.filter((w) =>
    seen.has(w.type) ? false : seen.add(w.type),
  );
  return [...firsts, ...open.filter((w) => !firsts.includes(w))].slice(0, 3);
}
