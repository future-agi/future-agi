// dataset integration FE-0: the live-connection command builders are removed (that
// command does not exist). What connectSteps.js still owns: agent specs,
// default agent names and the harness flags the iterate guard and the
// harness snippets read.
import { describe, expect, it } from "vitest";
import * as steps from "../connectSteps";
import { defaultAgentName, harnessFlag, specOf } from "../connectSteps";

describe("connectSteps after FE-0", () => {
  it("exports no connect command builder", () => {
    [
      "connectCommand",
      "connectSteps",
      "copyAllScript",
      "installLine",
      "serveFileOf",
      "withServeToken",
      "INSTALL_LINE",
      "NPM_INSTALL_LINE",
      "VOICE_CONNECT_SPEC",
    ].forEach((name) => expect(steps[name]).toBeUndefined());
  });

  it("keeps the agent spec of a code entry", () => {
    expect(specOf("langgraph")).toBe("agent.py:graph");
    expect(specOf(null, "typescript")).toBe("src/agent.ts:target");
  });

  it("names agents and harness flags", () => {
    expect(defaultAgentName({ kind: "code" })).toBe("my_agent");
    expect(defaultAgentName({ kind: "harness", entryId: "claude_code" })).toBe(
      "claude-code",
    );
    expect(harnessFlag("codex_cli")).toBe("codex");
  });
});
