/*
  The "from code" snippets — the same text wherever an environment is built
  from code: Build environment → From code, Get started, Settings → Developer.
  They follow the developer spec: the agent-learn CLI and the fi.simulate
  module, both installed by agent-learning-kit. Keep them in step with it.
*/
export const PYTHON_DEFINE = `from fi.simulate import Environment, World, Persona, Scenario, verifiers

env = Environment(
    name="my-environment",
    world=World.prebuilt("returns-desk"),   # or your own tools and state
    personas=[Persona("in-a-hurry", "Polite, wants it done now")],
    scenarios=Scenario.load("scenarios/"),  # one YAML per scenario
    verifiers=[verifiers.sub_goals(), verifiers.tool_calls()],
)

env.validate()                       # check before you run
env.run(agent=my_agent, repeats=3)   # evaluate your agent
env.push()                           # optional: versions here`;

export const AGENT_PROMPT =
  "Build an environment for my refund agent, run it three times against 40 scenarios including prompt-injection attacks, and fix whatever fails most.";

/* Install agent-learning-kit (the Python SDK and the agent-learn CLI), and
   sign in — once per machine. */
export const INSTALL_CMD =
  "pip install agent-learning-kit && agent-learn auth login";

/* The docs index written for coding agents. */
export const AGENT_DOCS_URL = "https://docs.futureagi.com/llms.txt";

export const MCP_ADD_CMD =
  "claude mcp add futureagi --transport http https://api.futureagi.com/mcp";

/* Training — the doc's one TRL path (Week 3): a reviewed experiment on this
   environment, then the trained model checked on held-out cases it never saw. */
export const trainCmd = (id = "my-environment") => `agent-learn train create --env ${id} \\
  --trainer trl --base-model <base-model> --mode rollout \\
  --train-split ${id}/train --dev-split ${id}/dev --holdout ${id}/holdout \\
  --reward ${id}/reward --resources ./resources.yaml --budget 5000
agent-learn train start <experiment-id>
agent-learn train status <experiment-id> --watch
agent-learn train pause <experiment-id>     # resume / cancel the same way, where the trainer supports it
agent-learn train results <experiment-id>   # held-out: trained model vs the original`;

/* A permitted copy of the environment for a trainer on another machine. */
export const serveCmd = (id = "my-environment") => `agent-learn env pull ${id}`;
