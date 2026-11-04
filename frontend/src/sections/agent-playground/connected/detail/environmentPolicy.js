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
export const policyLabel = (env, agent) => {
  if (env.policy !== "pinned" || !env.agentVersion)
    return "Default: production";
  const ahead = productionAhead(env, agent);
  const pin = `Default v${env.agentVersion.number}`;
  return ahead != null ? `${pin} · v${ahead} in production` : pin;
};

export const isBehind = (env, agent) =>
  env.policy === "pinned" && productionAhead(env, agent) != null;
