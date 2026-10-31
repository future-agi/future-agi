/*
  The "from code" snippets, verbatim from Vel's design (fi.simulate). The CLI
  and SDK aren't released yet, so these are what they will read.
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

export const MCP_ADD_CMD =
  "claude mcp add futureagi --transport http https://api.futureagi.com/mcp";

export const installCmd = (os) =>
  os === "windows" ? "py -m pip install futureagi" : "pip install futureagi";
