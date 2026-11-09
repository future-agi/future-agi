import { describe, expect, it } from "vitest";
import { runModelOf, sandboxText } from "../runWhere";
import { tracingOf, tracingWord } from "../runTracing";
import { provenanceOf } from "../provenanceModel";

describe("runModelOf (S5 precedence)", () => {
  it("engine stamp wins", () => {
    expect(
      runModelOf({
        config: { engine: { model: "a" }, harness: { model: "b" } },
      }),
    ).toBe("a");
  });
  it("harness > changed model param > prompt > agent node > models[]", () => {
    expect(runModelOf({ config: { harness: { model: "h" } } })).toBe("h");
    const params = {
      schema: [{ name: "model", default: "gpt-4.1-mini" }],
      values: { model: "gpt-4o-mini" },
    };
    expect(runModelOf({ config: { params, model: { id: "p" } } })).toBe(
      "gpt-4o-mini",
    );
    // Unchanged param: the declared default is not a choice.
    expect(
      runModelOf({
        config: {
          params: { ...params, values: { model: "gpt-4.1-mini" } },
          models: [
            { id: "judge-x", role: "helper" },
            { id: "m1", role: "main" },
          ],
        },
      }),
    ).toBe("m1");
    expect(runModelOf({ config: { model: { id: "p" } } })).toBe("p");
    expect(
      runModelOf({ config: { agent: { nodes: [{}, { model: "n" }] } } }),
    ).toBe("n");
    expect(runModelOf({ config: {} })).toBe(null);
  });
});

describe("sandboxText", () => {
  it("repo, image, network and vCPU", () => {
    expect(
      sandboxText({
        sandbox: {
          source: "image",
          image: "ghcr.io/x/bot:2",
          entry: "bot.py:run",
          network: "allowlist",
          allowlist: ["a", "b"],
          resources: { cpu: 4 },
        },
      }),
    ).toBe(
      "Future AGI sandbox · ghcr.io/x/bot:2 · bot.py:run · network allowlist (2) · 4 vCPU",
    );
    expect(
      sandboxText({
        harness: {
          sandbox: { repo: "acme/a", branch: "dev", network: "off", cpu: 8 },
        },
      }),
    ).toBe("Future AGI sandbox · acme/a@dev · network Off · 8 vCPU");
  });
  it("null without a sandbox object (seeded string sandboxes keep their line)", () => {
    expect(sandboxText({})).toBe(null);
    expect(sandboxText({ harness: { sandbox: "Docker · x:1" } })).toBe(null);
  });
});

describe("tracingWord", () => {
  it("config first, then the catalog badge", () => {
    expect(tracingWord({ config: { framework: { tracing: "manual" } } })).toBe(
      "manual spans",
    );
    expect(tracingOf({ config: { framework: { id: "agno" } } })).toBe("otel");
    expect(tracingWord({ config: { framework: { id: "crewai" } } })).toBe(
      "traceAI",
    );
    expect(tracingWord({ config: {} })).toBe("traceAI");
  });
});

describe("sandboxText from execution", () => {
  it("a seeded sandbox code run names its image, entry and network", () => {
    expect(
      sandboxText({
        execution: {
          mode: "sandbox",
          image: "python:3.12 + acme/support-agents@4be2d90",
          network: "off",
          cpu: 2,
        },
        code: { entry: "agent.py:agent" },
      }),
    ).toMatch(
      /^Future AGI sandbox · python:3\.12 \+ acme\/support-agents@4be2d90 · agent\.py:agent · network .+ · 2 vCPU$/,
    );
    expect(sandboxText({ execution: { mode: "local" } })).toBeNull();
  });
});

describe("sandboxText({fromExecution: false}) and the provenance line", () => {
  const seeded = {
    kind: "harness",
    execution: {
      mode: "sandbox",
      label: "Future AGI sandbox",
      image: "ghcr.io/x/sb:1",
      network: "off",
    },
  };
  it("reads only a sandbox the run was created with", () => {
    expect(sandboxText(seeded, { fromExecution: false })).toBeNull();
    expect(sandboxText(seeded)).toBe(
      "Future AGI sandbox · ghcr.io/x/sb:1 · network Off",
    );
    expect(
      sandboxText(
        { ...seeded, sandbox: { source: "image", image: "ghcr.io/y:2" } },
        { fromExecution: false },
      ),
    ).toBe("Future AGI sandbox · ghcr.io/y:2");
  });
  it("a created sandbox run's line names it; a seeded one keeps `Future AGI sandbox`", () => {
    const where = (config) => provenanceOf({ config }).where.text;
    expect(where(seeded)).toBe("Future AGI sandbox");
    expect(
      where({
        ...seeded,
        sandbox: { repo: "acme/a", branch: "dev", network: "off", cpu: 2 },
      }),
    ).toBe("Future AGI sandbox · acme/a@dev · network Off · 2 vCPU");
  });
});
