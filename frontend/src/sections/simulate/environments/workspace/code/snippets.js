import { HOST_API } from "src/config-global";

// The "Run from code" snippets. The API one is a real request against our
// run endpoint; the fai CLI and the fi.simulate SDK are Vel's design and
// don't exist yet, so their tabs are flagged UI only. Every snippet carries
// this environment's id and the repeats the header is set to.

const host = () => HOST_API || "https://api.futureagi.com";

export const apiSnippet = (
  id,
  repeats,
) => `# 1. The scenario keys to run (page with ?page= past 100)
curl -s "${host()}/simulate/api/harness-jobs/${id}/scenarios/?limit=100" \\
  -H "X-Api-Key: $FI_API_KEY" \\
  -H "X-Secret-Key: $FI_SECRET_KEY"

# 2. Start the run with those keys
curl -X POST "${host()}/simulate/api/harness-environments/${id}/run/" \\
  -H "X-Api-Key: $FI_API_KEY" \\
  -H "X-Secret-Key: $FI_SECRET_KEY" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: $(uuidgen)" \\
  -d '{"scenario_ids": ["<scenario_key>", "..."], "trials": ${repeats}}'`;

export const INSTALL_CMD = "pip install futureagi && fai login";

export const cliSteps = (id, repeats, parallel) => [
  {
    title: "Install and sign in",
    body: "Once per machine. Sign-in opens the browser; in CI use FI_API_KEY.",
    cmd: INSTALL_CMD,
  },
  {
    title: "Run this environment",
    body: "Runs every scenario against its agent, the same run as Run simulation.",
    hint: "--scenarios picks a subset; --agent-url tests a local build.",
    cmd: `fai sim run ${id} --repeats ${repeats} --parallel ${parallel}`,
  },
  {
    title: "Read the results",
    body: "Pass rate, the failing scenarios and why, with a link to the run here.",
    cmd: "fai sim results --latest --failures",
  },
];

export const pythonRun = (
  id,
  repeats,
  parallel,
) => `from fi.simulate import Environment

env = Environment.get("${id}")

run = env.run(repeats=${repeats}, parallel=${parallel})
for result in run.stream():          # each conversation as it ends
    print(result.scenario.slug, result.passed, result.score)

summary = run.wait()                 # pass rate, failures; run.url opens it here
print(summary.pass_rate, run.url)`;

export const pythonOwnAgent = (
  repeats,
) => `from fi.simulate import AgentInput, AgentResponse

def my_agent(input: AgentInput) -> AgentResponse:
    # call your framework here: OpenAI Agents SDK, LangGraph, CrewAI...
    reply, calls = call_my_framework(input.messages)
    return AgentResponse(content=reply, tool_calls=calls)

run = env.run(agent=my_agent, repeats=${repeats})`;

export const ciWorkflow = (
  id,
  repeats,
  parallel,
) => `# .github/workflows/simulate.yml
name: Simulate
on: [pull_request]
jobs:
  simulate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install futureagi
      - run: fai sim run ${id} --repeats ${repeats} --parallel ${parallel} --fail-under 0.9
        env:
          FI_API_KEY: \${{ secrets.FI_API_KEY }}`;

export const agentPrompt = (name, repeats) =>
  `Run ${name || "this environment"} with ${repeats} repeats and summarise what failed and why.`;

export const pythonTrain = (id) => `from fi.simulate import Environment

env = Environment.get("${id}")

obs = env.reset(seed=7)                   # one episode: a scenario played once
done = False
while not done:
    action = policy(obs)                  # a message and/or tool calls
    obs, reward, done, info = env.step(action)

# many episodes at once, written for your trainer
env.rollout(policy, episodes=10_000, parallel=256, out="rollouts.jsonl")`;

export const serveCmd = (id) => `fai env serve ${id} --replicas 256`;
