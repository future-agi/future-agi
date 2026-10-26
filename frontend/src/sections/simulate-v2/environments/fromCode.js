/*
  The "from code" snippets — the same text wherever an environment is built
  from code: Build environment → From code, Get started, Settings → Developer.
  They follow the developer spec (fi.simulate). Keep them in step with it.
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

/* Install the Python SDK and the fai CLI, and sign in — once per machine. */
export const INSTALL_CMD = "pip install futureagi && fai login";

/* The docs index written for coding agents. */
export const AGENT_DOCS_URL = "https://docs.futureagi.com/llms.txt";

export const MCP_ADD_CMD =
  "claude mcp add futureagi --transport http https://api.futureagi.com/mcp";

/* Training — the RL side of the same environment. */
export const pythonTrain = (
  id = "my-environment",
) => `from fi.simulate import Environment

env = Environment.get("${id}")

obs = env.reset(seed=7)                   # one episode: a scenario played once
done = False
while not done:
    action = policy(obs)                  # a message and/or tool calls
    obs, reward, done, info = env.step(action)

# many episodes at once, written for your trainer
env.rollout(policy, episodes=10_000, parallel=256, out="rollouts.jsonl")`;

export const serveCmd = (id = "my-environment") =>
  `fai env serve ${id} --replicas 256`;
