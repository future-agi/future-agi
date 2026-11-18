import { describe, it, expect } from "vitest";
import { GITHUB_REPOS } from "../integrations";
import { readAgentFolder } from "../connectAgent";
import { worldFor } from "../agentWorlds";

/* An agent's own name decides its world, not its repository's (19 Nov:
   "Order status helper" in acme-inc/billing-copilot got a banking world). */
describe("world read from a repository agent", () => {
  const repo = GITHUB_REPOS.find((r) => r.name === "acme-inc/billing-copilot");
  const agent = repo.agents.find((a) => a.id === "order-status");
  const folder = agent.entry.split("/").slice(0, -1).join("/");

  it("an order-status agent in a billing repo looks up orders", () => {
    const read = readAgentFolder({ repo, folder, branch: "main", entryId: agent.id });
    const tools = read.facts.tools.value.map((t) => (typeof t === "string" ? t : t.name));
    expect(tools).toContain("lookup_order");
    expect(tools).not.toContain("freeze_card");
    const world = worldFor({ kind: "repo", agentName: agent.name, repoUrl: `https://github.com/${repo.name}`, tools });
    expect(world.id).toBe("support");
  });

  it("the billing agent in the same repo still gets the billing world", () => {
    const billing = repo.agents.find((a) => a.id === "billing");
    const bf = billing.entry.split(":")[0].split("/").slice(0, -1).join("/");
    const read = readAgentFolder({ repo, folder: bf, branch: "main", entryId: billing.id });
    const tools = read.facts.tools.value.map((t) => (typeof t === "string" ? t : t.name));
    const world = worldFor({ kind: "repo", agentName: billing.name, repoUrl: `https://github.com/${repo.name}`, tools });
    expect(world.id).toBe("billing");
  });
});
