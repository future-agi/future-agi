// Where it runs › Remote endpoint and CI: what the editors emit reaches the
// run's config (options-truth L1, G2 contracts with G3/G4/G5). The endpoint
// editor's form becomes config.endpoint (the URL the run calls); CI's trigger
// and gate ride flat on the slot and become config.ci.
import { describe, expect, it } from "vitest";
import { endpointValueOf } from "../EndpointConfig";
import { ciValueOf } from "../CiConfig";
import { runConfigFromDraft } from "../sourceModel";

describe("Remote endpoint", () => {
  it("the editor's form is the run's endpoint", () => {
    const ep = endpointValueOf({
      url: " https://agent.acme.test/v2/reply ",
      method: "put",
      bodyTemplate: '{"q": "{{instruction}}"}',
      outputPath: "",
      headers: [
        { name: "Authorization", secret: "ACME_TOKEN" },
        { name: " ", secret: "" },
      ],
      streaming: true,
    });
    expect(ep).toEqual({
      protocol: "http",
      url: "https://agent.acme.test/v2/reply",
      method: "PUT",
      headers: [{ name: "Authorization", secret: "ACME_TOKEN" }],
      body_template: '{"q": "{{instruction}}"}',
      output_path: "$.reply",
      streaming: true,
      audio: "text",
    });
    const c = runConfigFromDraft({
      entryId: "langgraph",
      mode: "remote",
      slot: { endpoint: ep },
    });
    expect(c.endpoint.url).toBe("https://agent.acme.test/v2/reply");
    expect(c.endpoint.method).toBe("PUT");
  });
});

describe("CI settings reach the run", () => {
  it("a flat slot.ci (harness editor) becomes config.ci", () => {
    const ci = ciValueOf({ trigger: "push", threshold: 0.6 });
    ["claude_code", "langgraph"].forEach((entryId) => {
      const c = runConfigFromDraft({ entryId, mode: "ci", slot: { ci } });
      expect(c.ci).toMatchObject({ trigger: "push", threshold: 0.6 });
    });
  });
});

// dataset integration FE-0: the voice where-it-runs copy is gone with the voice card.
import { modeHint, whereLine } from "../WhereItRuns";

describe("where-it-runs copy has no voice branch", () => {
  it("a mode reads its generic hint and line", () => {
    expect(modeHint("remote", "voice", "vapi")).toBe(
      modeHint("remote", "http", "http"),
    );
    expect(whereLine("local", "langgraph")).toBe("Runs on this machine");
    expect(
      whereLine("local", "langgraph", { voice: { provider: "livekit" } }),
    ).toBe("Runs on this machine");
  });
});
