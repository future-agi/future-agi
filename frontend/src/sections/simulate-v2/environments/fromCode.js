/*
  The "from code" snippets — the same text wherever an environment is built
  from code: Build environment → From code, Get started, Settings → Developer.
  They follow the developer spec: the fi CLI and the fi.simulate
  module, both installed by futureagi. Keep them in step with it.
*/
/* The SDK only submits and watches; environments and runs live in the
   cloud (plan D01). Names are planned until the SDK ships. */
export const PYTHON_DEFINE = `from futureagi import Client

client = Client()  # FI_API_KEY

# Build in Future AGI: you review the proposed world and scenarios there
env = client.sim.environments.create(
    name="my-environment", repo=".", request="Refunds and cancellations",
)
env.wait_until_saved()

# Run your registered agent version against it — in the cloud
run = client.sim.runs.create(
    env=env.id, agent_version="<agent-version-id>", repeats=3,
)
run.wait()  # this only watches; closing it doesn't cancel the run
print(run.status, run.url)`;

export const AGENT_PROMPT =
  "Build an environment for my refund agent, run it three times against 40 scenarios including prompt-injection attacks, and fix whatever fails most.";

/* Install the fi CLI (install line is a placeholder until fi ships its
   installer), and
   sign in — once per machine. */
export const INSTALL_CMD = "brew install future-agi/tap/fi && fi auth login";

/* The docs index written for coding agents. */
export const AGENT_DOCS_URL = "https://docs.futureagi.com/llms.txt";

export const MCP_ADD_CMD =
  "claude mcp add futureagi --transport http https://api.futureagi.com/mcp";
