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

// The server moves an environment only once its build has finished, and refuses
// any other with a 409: a failed or cancelled one never, an unfinished one until it
// is done. So only a ready row offers a change that could otherwise only end there.
export const canFollow = (env) =>
  env.policy === "pinned" &&
  !!env.agentVersion &&
  env.status === ENV_STATUS.READY;
