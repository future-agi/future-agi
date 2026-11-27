import { describe, expect, it, vi } from "vitest";

import { validateContractedResponse } from "src/api/contracts/openapi-contract";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";
import { simulateApiAgentFixRunsSubmitAttempt } from "src/generated/api-contracts/api";
import generatedClient from "src/generated/api-contracts/api.ts?raw";
import {
  SimulateApiAgentFixRunsReadResponse,
  simulateApiAgentFixRunsCreateBodyBudgetCreditsMax,
  simulateApiAgentFixRunsReadResponseRequestOutsideCandidateOneNumberMax,
} from "src/generated/api-contracts/api.zod";
import {
  BUDGET_CREDITS_MAX,
  OUTSIDE_ATTEMPTS_MAX,
} from "src/api/simulate-environments/codingAgent";
import { fixtureFixRun } from "src/api/simulate-environments/__tests__/fixtures/fixRuns";

const sent = vi.hoisted(() => ({
  calls: [] as Array<{ url: string; options: RequestInit }>,
}));
vi.mock("src/api/contracts/openapi-mutator", () => ({
  apiMutator: async (url: string, options: RequestInit) => {
    sent.calls.push({ url, options });
    return { data: {}, status: 201, headers: {} };
  },
}));

import {
  ATTEMPT_ID,
  SOURCE_RUN_ID,
  fixtureBlockedBrief,
  fixtureBrief,
  fixtureVerdict,
} from "./fixtures/codingAgent";

const BASE = "/simulate/api/agent-fix-runs";

const check = (
  url: string,
  data: unknown,
): { ok: boolean; skipped?: boolean } =>
  validateContractedResponse({
    status: 200,
    data,
    config: { method: "get", url },
  });

// The fixtures the coding-agent UI tests read are the routes' real shapes.
describe("coding-agent fixtures against the OpenAPI contract", () => {
  it.each([
    ["a brief", fixtureBrief()],
    ["a blocked brief", fixtureBlockedBrief()],
  ])("%s", (_, data) => {
    const result = check(
      `${BASE}/coding-agent-brief/?source_run_id=${SOURCE_RUN_ID}`,
      data,
    );
    expect(result.skipped).toBeFalsy();
    expect(result.ok).toBe(true);
  });

  it("an attempt's verdict", () => {
    const result = check(`${BASE}/${ATTEMPT_ID}/verdict/`, fixtureVerdict());
    expect(result.skipped).toBeFalsy();
    expect(result.ok).toBe(true);
  });

  it("an ordinary Epoch's read, which sends outside_candidate as null", () => {
    const { request } = fixtureFixRun("fix-ready-archive");
    const parsed = SimulateApiAgentFixRunsReadResponse.shape.request.safeParse({
      ...request,
      outside_candidate: null,
    });
    expect(parsed.error?.issues).toBeUndefined();
    expect(parsed.data?.outside_candidate).toBeNull();
  });

  it("caps a run's attempts where the API caps an attempt's number", () => {
    expect(OUTSIDE_ATTEMPTS_MAX).toBe(
      simulateApiAgentFixRunsReadResponseRequestOutsideCandidateOneNumberMax,
    );
  });

  it("caps an attempt's budget where the API caps budget_credits", () => {
    expect(BUDGET_CREDITS_MAX).toBe(
      simulateApiAgentFixRunsCreateBodyBudgetCreditsMax,
    );
  });

  it("keeps the coding-agent setting typed and default-off on capabilities", () => {
    expect(
      OPENAPI_CONTRACT.definitions.CapabilitiesResponse.properties
        .coding_agent_attempts_enabled,
    ).toMatchObject({ type: "boolean", default: false });
  });
});

// The submit route as the generated client sends it: the tree must leave as a file.
describe("the generated client's coding-agent submit", () => {
  it("sends multipart form data with the tree's bytes", async () => {
    sent.calls.length = 0;
    const tree = new Blob(["tree bytes"], { type: "application/gzip" });
    await simulateApiAgentFixRunsSubmitAttempt({
      source_run_id: SOURCE_RUN_ID,
      tree,
      summary: "refund first",
    });
    const [call] = sent.calls;
    expect(call.url).toBe(`${BASE}/coding-agent-attempts/`);
    expect(call.options.body).toBeInstanceOf(FormData);
    const form = call.options.body as FormData;
    expect(form.get("source_run_id")).toBe(SOURCE_RUN_ID);
    expect(form.get("tree")).toBeInstanceOf(Blob);
  });

  it("says a submitted change runs the agent and uses credits", () => {
    const doc = generatedClient.slice(
      generatedClient.lastIndexOf(
        "/**",
        generatedClient.indexOf(
          "export const simulateApiAgentFixRunsSubmitAttempt =",
        ),
      ),
      generatedClient.indexOf(
        "export const simulateApiAgentFixRunsSubmitAttempt =",
      ),
    );
    expect(doc).toContain("uses credits");
    expect(doc).not.toContain("calls no model");
  });
});
