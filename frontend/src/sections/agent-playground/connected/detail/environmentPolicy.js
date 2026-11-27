import { ENV_STATUS } from "src/sections/simulate/environments/myEnvironments.constants";

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

// A failed or cancelled build never moves: the server refuses it as not ready, so the
// row offers nothing that could only end in that error. A running one keeps the offer.
const NEVER_MOVES = new Set([ENV_STATUS.FAILED, ENV_STATUS.CANCELLED]);

export const canFollow = (env) =>
  env.policy === "pinned" && !!env.agentVersion && !NEVER_MOVES.has(env.status);
