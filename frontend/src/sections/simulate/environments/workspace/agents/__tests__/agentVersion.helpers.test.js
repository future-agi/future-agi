import { describe, it, expect } from "vitest";
import {
  deriveAgentName,
  deriveTypeLine,
  repoNameFrom,
  endpointNameFrom,
  short,
  connectionRowsFor,
  sourceRowsFor,
  normalizeAgentVersions,
  toEnvAgentVersion,
  mintNextVersion,
  applyActiveVersion,
  buildVersionSwitchSteps,
  buildVersionUpgradeSteps,
  buildPromoteSteps,
} from "../agentVersion.helpers";

const AT = "2026-09-17T00:00:00.000Z";

describe("string helpers", () => {
  it("short strips protocol and www", () => {
    expect(short("https://www.example.com/x")).toBe("example.com/x");
    expect(short("")).toBe("");
  });

  it("repoNameFrom takes the last path segment without .git", () => {
    expect(repoNameFrom("https://github.com/acme/support-bot.git")).toBe("support-bot");
    expect(repoNameFrom("")).toBe("repo");
  });

  it("endpointNameFrom takes the last path segment, else the host", () => {
    expect(endpointNameFrom("https://api.acme.com/v1/agent")).toBe("agent");
    expect(endpointNameFrom("https://api.acme.com")).toBe("api.acme.com");
    expect(endpointNameFrom("not a url")).toBe("not a url");
  });
});

describe("deriveAgentName", () => {
  it("prefers an explicit id, then a repo name, then an endpoint name", () => {
    expect(deriveAgentName({ values: { agentId: "asst_9" } })).toBe("asst_9");
    expect(deriveAgentName({ values: { repoUrl: "https://github.com/acme/bot" } })).toBe("bot");
    expect(deriveAgentName({ values: { endpoint: "https://api.acme.com/v1/agent" } })).toBe("agent");
  });

  it("falls back to a stable type-stem + id suffix", () => {
    const type = { label: "Voice agent" };
    expect(deriveAgentName({ id: "agent-abc123", values: {} }, type)).toBe("voice-abc123");
  });
});

describe("deriveTypeLine", () => {
  it("joins the type label with the provider", () => {
    const type = { label: "Voice" };
    expect(deriveTypeLine({ values: { provider: "vapi" } }, type)).toBe("Voice · vapi");
  });

  it("shows the repo branch when there is no provider", () => {
    const type = { label: "Repo" };
    expect(deriveTypeLine({ values: { repoUrl: "x" } }, type)).toBe("Repo · branch: main");
  });
});

describe("connectionRowsFor", () => {
  it("shapes a github/repo agent", () => {
    const rows = connectionRowsFor(
      {},
      { label: "Repo" },
      { repoUrl: "https://github.com/acme/bot", ref: { kind: "branch", value: "main" } },
    );
    expect(rows).toEqual([
      { label: "Kind", value: "Repository" },
      { label: "URL", value: "https://github.com/acme/bot", mono: true },
      { label: "Pinned to", value: "branch · main", mono: true },
    ]);
  });

  it("shapes an endpoint agent", () => {
    const rows = connectionRowsFor({}, { label: "Endpoint" }, { endpoint: "https://api.acme.com/agent" });
    expect(rows).toEqual([
      { label: "Kind", value: "Running endpoint" },
      { label: "URL", value: "https://api.acme.com/agent", mono: true },
    ]);
  });
});

describe("sourceRowsFor", () => {
  it("lists the repo location for a github agent", () => {
    const rows = sourceRowsFor({ values: { repoUrl: "https://github.com/acme/bot" }, via: "GitHub App" });
    expect(rows).toContainEqual({ label: "Location", value: "https://github.com/acme/bot", mono: true });
    expect(rows).toContainEqual({ label: "Attached via", value: "GitHub App" });
  });

  it("lists the endpoint location for an endpoint agent", () => {
    const rows = sourceRowsFor({ values: { endpoint: "https://api.acme.com/agent" } });
    expect(rows).toContainEqual({ label: "Location", value: "https://api.acme.com/agent", mono: true });
  });

  it("explains how an agent with no location was read", () => {
    const rows = sourceRowsFor({ values: {} });
    expect(rows).toEqual([
      { label: "How we read it", value: "Read from the imports and call sites in the source." },
    ]);
  });
});

describe("normalizeAgentVersions", () => {
  it("wraps a bare agent as its own v1", () => {
    const out = normalizeAgentVersions({ values: { endpoint: "x" }, connectedAt: AT });
    expect(out.activeVersionId).toBe("v1");
    expect(out.versions).toEqual([
      { id: "v1", label: "v1", values: { endpoint: "x" }, via: undefined, connectedAt: AT, note: "Initial version" },
    ]);
  });

  it("passes an already-normalised agent through unchanged (idempotent)", () => {
    const agent = { versions: [{ id: "v1", label: "v1" }], activeVersionId: "v1" };
    expect(normalizeAgentVersions(agent)).toBe(agent);
  });

  it("returns a falsy agent as-is", () => {
    expect(normalizeAgentVersions(null)).toBeNull();
  });
});

describe("toEnvAgentVersion", () => {
  it("maps a source version record onto the env-level shape", () => {
    const out = toEnvAgentVersion({ id: "v2", label: "v2", note: "n", via: "endpoint", connectedAt: AT });
    expect(out).toEqual({ id: "v2", label: "v2", note: "n", reach: "endpoint", createdAt: AT });
  });
});

describe("mintNextVersion", () => {
  it("increments the label off the existing stack (v1 -> v2)", () => {
    const agent = { versions: [{ id: "v1", label: "v1" }] };
    const next = mintNextVersion(agent, { values: { endpoint: "x" }, via: "endpoint", connectedAt: AT });
    expect(next).toEqual({
      id: "v2",
      label: "v2",
      values: { endpoint: "x" },
      via: "endpoint",
      connectedAt: AT,
      note: "Version 2",
    });
  });

  it("mints v1 for an agent with no versions yet", () => {
    const next = mintNextVersion({}, { connectedAt: AT });
    expect(next.id).toBe("v1");
    expect(next.label).toBe("v1");
  });
});

describe("applyActiveVersion", () => {
  it("copies the selected version's values up to the top level", () => {
    const agent = {
      versions: [
        { id: "v1", values: { endpoint: "old" }, via: "a", connectedAt: AT, note: "one" },
        { id: "v2", values: { endpoint: "new" }, via: "b", connectedAt: AT, note: "two" },
      ],
      activeVersionId: "v2",
    };
    const out = applyActiveVersion(agent);
    expect(out.values).toEqual({ endpoint: "new" });
    expect(out.via).toBe("b");
    expect(out.note).toBe("two");
  });

  it("falls back to the first version when the active id is missing", () => {
    const agent = {
      versions: [{ id: "v1", values: { endpoint: "first" } }],
      activeVersionId: "gone",
    };
    expect(applyActiveVersion(agent).values).toEqual({ endpoint: "first" });
  });
});

describe("build*Steps", () => {
  const agent = { typeId: "voice", versions: [{ id: "v1", label: "v1" }, { id: "v2", label: "v2" }] };
  const to = { id: "v2", label: "v2", via: "endpoint" };
  const from = { id: "v1", label: "v1" };

  it("buildVersionSwitchSteps opens with a switch think step and narrates the target", () => {
    const steps = buildVersionSwitchSteps({ agent, from, to, isRollback: false });
    expect(steps[0].kind).toBe("think");
    expect(steps[0].text).toContain("Switching to");
    expect(steps[0].text).toContain("v2");
    expect(steps.some((s) => s.kind === "note")).toBe(true);
  });

  it("buildVersionSwitchSteps reads as a rollback when flagged", () => {
    const steps = buildVersionSwitchSteps({ agent, from, to, isRollback: true });
    expect(steps[0].text).toContain("Rolling back to");
  });

  it("buildVersionUpgradeSteps includes a contract-diff json step", () => {
    const steps = buildVersionUpgradeSteps({ agent, next: to });
    expect(steps.some((s) => s.kind === "json")).toBe(true);
    expect(steps[0].kind).toBe("think");
  });

  it("buildPromoteSteps opens by setting the new source and includes a diff", () => {
    const steps = buildPromoteSteps({ from: agent, to: { typeId: "voice", via: "endpoint" } });
    expect(steps[0].text).toContain("Setting");
    expect(steps.some((s) => s.kind === "json")).toBe(true);
  });
});
