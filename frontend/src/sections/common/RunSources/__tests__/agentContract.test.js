// IT-R3 G1 P2: `Point it at your agent` states the contract in three lines.
import { describe, expect, it } from "vitest";
import { catalogEntry } from "../catalog";
import { agentContract } from "../snippets/runLocal";
import { pySample } from "../snippets/agentSamples";

const SB = { envVar: "ACME_SANDBOX_URL" };

describe("agentContract", () => {
  it("LangGraph: the call, the answer, the tools and what to edit", () => {
    const { lines } = agentContract({
      entry: catalogEntry("langgraph"),
      lang: "python",
      ids: { inputColumn: "instruction", sandbox: SB },
    });
    expect(lines).toEqual([
      'We call graph.invoke({"messages": [("user", <instruction>)]}) once per row and read the last message as the answer.',
      "Your tools call $ACME_SANDBOX_URL/tools/<name>, so the checks can read the end state.",
      "Different input or output shape? Edit the two lines of run_rows() in run_and_upload.py that call your agent and read its answer.",
    ]);
  });
  it("TypeScript names its runner and function; no sandbox, no tools line", () => {
    const { lines } = agentContract({
      entry: catalogEntry("mastra"),
      lang: "typescript",
      ids: {},
    });
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatch(/<input>/);
    expect(lines[1]).toMatch(/runRows\(\) in run_and_upload\.ts/);
  });
});

// options-truth L2 G4 (C2): the six frameworks' samples are runnable code
// that defines exactly the variable the runner's contract line calls.
describe("framework samples are real (no `x = ...`)", () => {
  const SB = {
    envVar: "ACME_SANDBOX_URL",
    tools: ["lookup_order", "update_address", "issue_refund", "search_kb"],
  };
  it.each([
    ["llamaindex", /^agent = FunctionAgent\(/m],
    ["google_adk", /^root_agent = LlmAgent\(/m],
    ["autogen", /^team = RoundRobinGroupChat\(/m],
    ["smolagents", /^agent = CodeAgent\(/m],
    ["dspy", /^program = dspy\.ReAct\(/m],
    ["haystack", /^pipeline = Pipeline\(\)/m],
  ])("%s", (id, defines) => {
    const entry = catalogEntry(id);
    const code = pySample(id, { sandbox: SB }).join("\n");
    expect(code).toMatch(defines);
    expect(code).not.toMatch(/^\w+ = \.\.\.$/m);
    // the variable the runner imports (--agent file:var) is defined
    const attr = entry.agent.spec.split(":")[1];
    expect(code).toMatch(new RegExp(`^${attr} = `, "m"));
    // the tools are the suite's, each one POST to the sandbox
    expect(code).toContain(
      'SANDBOX = os.environ.get("ACME_SANDBOX_URL", "http://127.0.0.1:8765")',
    );
    expect(code).toMatch(/def lookup_order\(order_id: str\) -> dict:/);
    // the comment quotes the runner's own call (ADK: its run_once helper)
    expect(code).toContain(
      id === "google_adk" ? "(run_once)" : entry.agent.call,
    );
  });
});
