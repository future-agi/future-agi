/*
  What the ready environments' browser filters and labels by.
*/

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
export const domainLabelOf = (d) =>
  DOMAIN_LABEL[d] || (d ? d.charAt(0).toUpperCase() + d.slice(1) : "Other");

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
