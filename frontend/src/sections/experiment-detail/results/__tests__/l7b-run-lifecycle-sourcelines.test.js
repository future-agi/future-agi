// options-truth L7b G4: Run details names what an MCP server tool / A2A run
// called (Review's words), the output format it recorded, and the change
// chips compare the MCP env secrets and the output format.
import { describe, expect, it } from "vitest";
import { a2aSourceLine, mcpSourceLine, sourceLine } from "../runSourceLines";
import { configDiff } from "src/sections/common/Runs/configDiff";
import { recordedMachineText } from "src/sections/common/Runs/provenanceModel";

const mcpConfig = (env) => ({
  kind: "mcp_tool",
  mcp_tool: {
    server: {
      name: "acme-support-sandbox",
      transport: "stdio",
      command: "npx acme-support-mcp",
      ...(env ? { env } : {}),
    },
    tool: "lookup_order",
    arguments: { order_id: "{{order_id}}" },
  },
  safety: { side_effects: "stubs" },
});
const run = (config) => ({
  run_id: "r",
  target_type: "http",
  target: { method: "POST", url: "stdio:npx acme-support-mcp" },
  config,
});

describe("Run details source line (MCP / A2A / output format)", () => {
  it("an MCP run names server, transport, tool, env secret and side effects", () => {
    const c = mcpConfig([
      { name: "ACME_SANDBOX_TOKEN", secret: "FI_PROJECT_KEY" },
    ]);
    expect(mcpSourceLine(c)).toBe(
      "MCP · acme-support-sandbox (stdio) · lookup_order · env ACME_SANDBOX_TOKEN ← secret FI_PROJECT_KEY · Stubbed side effects",
    );
    expect(sourceLine(run(c))).toBe(mcpSourceLine(c));
    expect(sourceLine(run(c))).not.toMatch(/Endpoint|POST/);
  });

  it("without an env secret the line says nothing about env", () => {
    expect(mcpSourceLine(mcpConfig())).toBe(
      "MCP · acme-support-sandbox (stdio) · lookup_order · Stubbed side effects",
    );
  });

  it("an A2A run names the card and skill", () => {
    const c = {
      kind: "a2a",
      a2a: {
        card_url: "https://help.globex.test/.well-known/agent.json",
        card: { name: "Globex Help Desk" },
        skill_id: "refund_request",
      },
    };
    expect(a2aSourceLine(c)).toBe(
      "A2A · Globex Help Desk · skill refund_request",
    );
    expect(sourceLine(run(c))).toBe(a2aSourceLine(c));
  });

  it("an HTTP endpoint run keeps its endpoint line", () => {
    expect(sourceLine(run({ kind: "http" }))).toBe(
      "Endpoint · POST stdio:npx acme-support-mcp",
    );
  });

  it("the recorded output format ends the line", () => {
    const r = {
      run_id: "p",
      target_type: "prompt",
      target: { prompt_name: "Describe", model: "gpt-4o" },
      config: { kind: "prompt", output_format: "json" },
    };
    expect(sourceLine(r)).toMatch(/ · Output json$/);
    expect(sourceLine({ ...r, config: { kind: "prompt" } })).not.toMatch(
      /Output/,
    );
  });
});

describe("configDiff: MCP env secret and output format", () => {
  it("an added env secret is an MCP chip", () => {
    const chips = configDiff(
      mcpConfig(),
      mcpConfig([{ name: "ACME_SANDBOX_TOKEN", secret: "FI_PROJECT_KEY" }]),
    );
    const env = chips.find((c) => c.axis === "mcp" && c.detail?.env);
    expect(env?.label).toBe(
      "MCP env none → ACME_SANDBOX_TOKEN ← FI_PROJECT_KEY",
    );
  });

  it("the same env gives no chip", () => {
    const e = [{ name: "ACME_SANDBOX_TOKEN", secret: "FI_PROJECT_KEY" }];
    expect(
      configDiff(mcpConfig(e), mcpConfig(e)).filter((c) => c.detail?.env),
    ).toEqual([]);
  });

  it("a changed output format is a params chip", () => {
    const chips = configDiff(
      { kind: "prompt", output_format: "string" },
      { kind: "prompt", output_format: "json" },
    );
    expect(chips.map((c) => c.label)).toContain("output string → json");
  });
});

describe("recordedMachineText", () => {
  it("names the machine and owner a local run recorded", () => {
    expect(
      recordedMachineText({
        where: { mode: "local", label: "voice-worker@acme-gpu-01" },
        owner: { name: "Ravi Menon" },
      }),
    ).toBe("Ran on voice-worker@acme-gpu-01 (Ravi Menon)");
    expect(
      recordedMachineText({ where: { mode: "remote", label: "x" } }),
    ).toBeNull();
  });
});
