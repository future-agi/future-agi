// The production version, when it has moved past the one this environment
// is pinned to; null when the environment tests the latest in production.
export const productionAhead = (env, agent) => {
  const pin = env.agentVersion;
  const production = agent?.activeVersionNumber ?? null;
  if (!pin || production == null || production <= pin.number) return null;
  return production;
};

// What the environment runs by default, as the design words it: production,
// or a pinned version, naming production when it has moved on.
export const offProduction = (env, agent) => {
  const on = env.agentVersion?.number;
  const production = agent?.activeVersionNumber ?? null;
  if (on == null || production == null || production === on) return null;
  return production;
};

const followLabel = (env, agent) => {
  if (!env.agentVersion) return "Following production";
  const production = offProduction(env, agent);
  return production != null
    ? `Follows production · on v${env.agentVersion.number}, v${production} offered`
    : `Following production (v${env.agentVersion.number})`;
};

export const policyLabel = (env, agent) => {
  if (env.policy !== "pinned") return followLabel(env, agent);
  if (!env.agentVersion) return "Default: production";
  const ahead = productionAhead(env, agent);
  const pin = `Default v${env.agentVersion.number}`;
  return ahead != null ? `${pin} · v${ahead} in production` : pin;
};

export const isBehind = (env, agent) =>
  env.policy === "pinned"
    ? productionAhead(env, agent) != null
    : offProduction(env, agent) != null;

export const canFollow = (env, agent) =>
  env.policy === "pinned"
    ? !!env.agentVersion
    : offProduction(env, agent) != null;
