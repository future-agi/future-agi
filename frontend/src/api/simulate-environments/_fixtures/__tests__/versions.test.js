import { describe, it, expect } from "vitest";
import {
  environmentVersions,
  agentVersionsWithRuns,
  nextAgentVersion,
  versionNumber,
} from "../versions";

const env = { id: "env-1" };

describe("versions", () => {
  it("seeds v3/v2/v1 with current on the newest", () => {
    const list = environmentVersions(env, { scenarios: [{}, {}, {}, {}] });
    expect(list.map((v) => v.label)).toEqual(["v3", "v2", "v1"]);
    expect(list.find((v) => v.current).label).toBe("v3");
  });

  it("marks the active env version as current", () => {
    const list = environmentVersions(env, {
      scenarios: [{}, {}, {}, {}],
      activeEnvVersion: "v1",
    });
    expect(list.find((v) => v.current).label).toBe("v1");
  });

  it("counts runs by agent version label", () => {
    const envState = {
      agentVersions: [
        { id: "agent-v1", label: "v1" },
        { id: "agent-v2", label: "v2" },
      ],
      runs: [
        { id: "r1", agentVersion: "v1" },
        { id: "r2", agentVersion: "v1" },
        { id: "r3", agentVersion: "v2" },
      ],
    };
    const list = agentVersionsWithRuns(envState);
    const byLabel = Object.fromEntries(list.map((v) => [v.label, v.runs]));
    expect(byLabel.v1).toBe(2);
    expect(byLabel.v2).toBe(1);
    expect(list[0].label).toBe("v2");
    expect(list[0].current).toBe(true);
  });

  it("increments the next agent version label", () => {
    const envState = { agentVersions: [{ id: "agent-v1", label: "v1" }] };
    expect(nextAgentVersion(envState).label).toBe("v2");
    expect(versionNumber("v3")).toBe(3);
  });
});
