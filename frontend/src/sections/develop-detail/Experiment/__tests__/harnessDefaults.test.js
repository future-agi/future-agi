import { describe, expect, it } from "vitest";
import {
  SEED_INSTRUCTIONS,
  imageName,
  modelsFor,
  sandboxFrom,
  wordCount,
} from "../harnessDefaults";

describe("harnessDefaults", () => {
  it("seeds a 412-word instructions file", () => {
    expect(wordCount(SEED_INSTRUCTIONS)).toBe(412);
    expect(
      SEED_INSTRUCTIONS.startsWith(
        "You are Acme's support agent. Use the acme-support-sandbox tools",
      ),
    ).toBe(true);
  });
  it("filters models by harness", () => {
    expect(modelsFor("Claude Code")[0]).toBe("claude-sonnet-4-5");
    expect(modelsFor("Codex CLI")[0]).toBe("gpt-5-codex");
  });
  it("sandbox from the dataset environment, else the default", () => {
    const env = {
      image: "ghcr.io/acme/acme-support-sandbox:1.4",
      resources: { cpu: 2, memory_gb: 4 },
      mcp: {
        name: "acme-support-sandbox",
        tools: 12,
        tool_names: ["lookup_order"],
      },
      secrets: ["ACME_SANDBOX_TOKEN"],
    };
    expect(imageName(env.image)).toBe("acme-support-sandbox:1.4");
    expect(sandboxFrom(env)).toMatchObject({
      fromDataset: true,
      sandbox: "Docker · acme-support-sandbox:1.4",
      resources: "2 CPU · 4 GB",
      mcp: { name: "acme-support-sandbox", tools: 12 },
      secrets: ["ACME_SANDBOX_TOKEN"],
    });
    expect(sandboxFrom(null)).toMatchObject({
      fromDataset: false,
      sandbox: "Default sandbox · python:3.12-slim",
      mcp: null,
    });
  });
});
