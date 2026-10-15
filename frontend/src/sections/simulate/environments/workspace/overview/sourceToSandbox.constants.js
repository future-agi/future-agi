export const MAP_COPY = {
  title: "How the world was built",
  subtitle:
    "Every fact ALK read from your agent, where it was read from, and what it became in the sandbox.",
  readHead: "Read from source",
  sandboxHead: "In the sandbox world",
  groups: {
    tools: { label: "Tools", hint: "from the agent's tool list" },
    rules: { label: "Rules", hint: "hard constraints the agent must hold" },
    stores: { label: "Stores", hint: "seeded before every run" },
    actors: { label: "Actors", hint: "named in the agent's instructions" },
  },
  mapped: (n) => `${n} mapped`,
  storesEmpty: "No stores seeded for this environment.",
  effectTarget: {
    read: "reads from the sandbox",
    write: "writes to the sandbox",
    external: "calls an external service",
    control: "controls the call",
  },
  notClassified: "effect not reported",
  enforcedBy: (kinds) => `enforced by ${kinds.join(", ")}`,
  checkedBy: (subGoal) => `checked by ${subGoal}`,
  notEnforced: "enforcement not reported",
  storeRows: (rows) => `${rows.toLocaleString()} rows seeded`,
  startingRows: (rows) => `${rows.toLocaleString()} rows in starting data`,
};

const originOf = (origin) => (origin?.kind ? origin : null);

export const toolMapRows = (env) =>
  (env?.tools || []).map((tool) => ({
    key: tool.name,
    name: tool.name,
    origin: originOf(tool.origin),
    target: MAP_COPY.effectTarget[tool.effectKind] || null,
    fallback: MAP_COPY.notClassified,
  }));

const ruleTarget = (detail) =>
  [
    detail?.enforced_by?.length ? MAP_COPY.enforcedBy(detail.enforced_by) : null,
    detail?.sub_goal ? MAP_COPY.checkedBy(detail.sub_goal) : null,
  ]
    .filter(Boolean)
    .join(" · ") || null;

export const ruleMapRows = (env) =>
  (env?.rules || []).map((text, index) => {
    const detail = (env?.ruleDetails || []).find((candidate) => candidate.text === text);
    return {
      key: `rule-${index}`,
      name: detail?.short || text,
      detail: detail?.short ? text : null,
      prose: !detail?.short,
      origin: originOf(detail?.origin),
      target: ruleTarget(detail),
      fallback: MAP_COPY.notEnforced,
    };
  });

const storeRow = (key, table) => {
  const rows = Number(table?.rows) || 0;
  return { key, name: table?.name, origin: null, target: MAP_COPY.storeRows(rows) };
};

export const storeMapRows = (env) =>
  (env?.seed?.tables || []).map((table) => storeRow(table.name, table));

export const storeRowsFromStores = (stores = []) =>
  (Array.isArray(stores) ? stores : []).flatMap((store) =>
    (store?.tables || [])
      .filter((table) => !table?.internal)
      .map((table) =>
        storeRow(`${store?.capability || store?.engine || "store"}:${table?.name}`, table),
      ),
  );

export const startingDataRows = (env, taken = new Set()) => {
  const base = env?.baseEnvironment;
  if (!base || typeof base !== "object" || Array.isArray(base)) return [];
  return Object.entries(base)
    .filter(([name, rows]) => Array.isArray(rows) && !taken.has(name))
    .map(([name, rows]) => ({
      key: `starting:${name}`,
      name,
      origin: null,
      target: MAP_COPY.startingRows(rows.length),
    }));
};

export const actorMapRows = (env) =>
  (env?.actors || []).map((actor) => ({
    key: actor.name,
    name: actor.name,
    origin: originOf(actor.origin),
    target: actor.role || null,
    fallback: "",
  }));
