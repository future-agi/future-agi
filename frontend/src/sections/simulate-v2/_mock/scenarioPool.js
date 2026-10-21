import { generatedPool } from "./scenarios";
import { sandboxScenarioPool } from "./twins";

/*
  Where new scenarios come from when chat or Generate is asked for more.
  A SaaS sandbox writes from its own apps (Gmail, Slack…); any other
  environment from the pool its world derives.
*/
export const scenarioPoolFor = (env, envState) => {
  const services = envState?.twinBacking?.services || [];
  return services.length ? sandboxScenarioPool(services, envState?.scenarios) : generatedPool(env);
};
