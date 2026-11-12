// New experiment drawer copy outside step 1 names only what the server's
// run-source report lets this workspace run (GET /api/capabilities/
// experiment_run_sources): the Edit experiment helper and the estimate's
// `How is this estimated?` text.
import { describe, expect, it, vi } from "vitest";
import {
  FAILED_AVAILABILITY,
  LOADING_AVAILABILITY,
  availabilityOfReport,
} from "src/sections/common/RunSources/runSourceAvailability";
import { editHelpOf } from "src/sections/common/RunSources/catalog";
import { PROMPT_CONFIG_TYPE } from "../constants";
import { estimateHelpOf } from "../costEstimateModel";
import { newRunCard } from "../experimentFormValues";

vi.mock("src/launch/launchSwitch", async (importOriginal) => ({
  ...(await importOriginal()),
  isSurfaceShown: () => true,
}));

const YES = { available: true, reason: null };
const NO = (reason) => ({ available: false, reason });
const ready = (sources = {}) =>
  availabilityOfReport({
    workspace_id: "00000000-0000-4000-8000-000000000001",
    sources: {
      prompt: YES,
      agent: YES,
      http: YES,
      a2a: NO("A2A experiments are not built in this release."),
      mcp_tool: NO("MCP tool experiments are not built in this release."),
      ...sources,
    },
  });
const AGENT_OFF =
  "Agent graph experiments are unavailable on this server. Contact your administrator.";
const PROTOCOLS = /A2A|MCP/;

describe("the Edit experiment helper", () => {
  it("names the endpoint the server runs and no A2A or MCP source", () => {
    const text = editHelpOf({
      datasetName: "Support tickets",
      availability: ready(),
    });
    expect(text).toBe(
      "Run prompts, agent graphs or endpoints on Support tickets. Every run is scored by the same evals.",
    );
    expect(text).not.toMatch(PROTOCOLS);
  });

  it.each([
    ["loading", LOADING_AVAILABILITY],
    ["failed", FAILED_AVAILABILITY],
    ["not passed", undefined],
  ])("names no Already running source while the report is %s", (_, a) => {
    expect(editHelpOf({ availability: a })).toBe(
      "Run prompts and agent graphs on this dataset. Every run is scored by the same evals.",
    );
  });

  it("names no Agent graph once the server refuses it", () => {
    expect(
      editHelpOf({
        datasetName: "Support tickets",
        availability: ready({ agent: NO(AGENT_OFF) }),
      }),
    ).toBe(
      "Run prompts or endpoints on Support tickets. Every run is scored by the same evals.",
    );
  });

  it("names every source when the server runs them all", () => {
    expect(
      editHelpOf({
        datasetName: "Support tickets",
        availability: ready({ a2a: YES, mcp_tool: YES }),
      }),
    ).toBe(
      "Run prompts, agent graphs, endpoints, A2A agents or MCP tools on Support tickets. Every run is scored by the same evals.",
    );
  });
});

describe("the estimate method text", () => {
  it("bills an HTTP-only plan's hosted source in words that name no protocol", () => {
    const lines = estimateHelpOf({
      promptConfig: [newRunCard(PROMPT_CONFIG_TYPE.HTTP)],
    });
    expect(lines).toContain(
      "A source you host bills you for its own calls. Its cost cap counts judge spend: the run stops once the next try's judges would pass the cap, so only the tries under it run and are judged.",
    );
    expect(lines.join(" ")).not.toMatch(PROTOCOLS);
  });

  it("names no protocol for an empty plan either", () => {
    expect(estimateHelpOf({ promptConfig: [] }).join(" ")).not.toMatch(
      PROTOCOLS,
    );
  });
});
