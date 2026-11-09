import { describe, expect, it } from "vitest";

import {
  AGENT_INFRASTRUCTURE_SUBHEADER,
  withAgentInfrastructure,
} from "../agent-infrastructure-nav";

const HOST_SECTIONS = [
  { items: [{ title: "Falcon AI", path: "/dashboard/falcon-ai" }] },
  { subheader: "BUILD", items: [{ title: "Dataset", path: "/x" }] },
  { subheader: "OBSERVE", items: [{ title: "Tracing", path: "/y" }] },
];

describe("withAgentInfrastructure", () => {
  it("leaves the host sidebar untouched when the flag is off", () => {
    expect(withAgentInfrastructure(HOST_SECTIONS, false)).toBe(HOST_SECTIONS);
  });

  it("adds Studio, Deployments, Sandboxes and Hub right after BUILD", () => {
    const sections = withAgentInfrastructure(HOST_SECTIONS, true);
    expect(sections.map((s) => s.subheader)).toEqual([
      undefined,
      "BUILD",
      AGENT_INFRASTRUCTURE_SUBHEADER,
      "OBSERVE",
    ]);
    expect(sections[2].items.map((i) => [i.title, i.path])).toEqual([
      ["Studio", "/dashboard/studio"],
      ["Deployments", "/dashboard/deployments"],
      ["Sandboxes", "/dashboard/sandboxes"],
      ["Hub", "/dashboard/hub"],
    ]);
    // The input list is not mutated.
    expect(HOST_SECTIONS).toHaveLength(3);
  });

  it("appends the group when there is no BUILD group", () => {
    const sections = withAgentInfrastructure([{ items: [] }], true);
    expect(sections.at(-1).subheader).toBe(AGENT_INFRASTRUCTURE_SUBHEADER);
  });
});
