import { describe, expect, it } from "vitest";

import {
  SimulateApiAgentFixRunsBriefResponse,
  SimulateApiAgentFixRunsReadResponse,
  SimulateApiAgentFixRunsVerdictResponse,
} from "src/generated/api-contracts/api.zod";
import { codingAgentPrompt } from "src/api/simulate-environments/codingAgent";
import { attemptChecks } from "src/sections/simulate/environments/workspace/improvements/attemptVerdictView";
import { endingSentence } from "src/sections/simulate/environments/workspace/improvements/fixRunView";
import type {
  AgentFixAttemptVerdictApi,
  AgentFixRunDetailApi,
} from "src/generated/api-contracts/api.schemas";

import {
  CHECK_ROWS,
  ENDING_SENTENCE,
  ENV_ID,
  attemptEpoch,
  attemptVerdict,
  codingAgentBrief,
  copiedPrompt,
  instructions,
  type Ids,
} from "../../../../../e2e/flows/simulate/coding-agent-attempt.payload";

// SIM-E2E-043 serves these from the browser, because the E2E stack has no Enterprise
// license and no runner, so it cannot hold an attempt's Epoch. Parsing them with the
// generated contract keeps them true to the API: a field the API renames or drops
// fails here.

// The stubs use the prebuilt template's id for the environment, the only one the
// E2E stack opens an epoch page under; the contract's ids are UUIDs.
const ENV_UUID = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b";
const asContract = (stub: unknown): unknown =>
  JSON.parse(JSON.stringify(stub).replaceAll(`"${ENV_ID}"`, `"${ENV_UUID}"`));

const ids: Ids = {
  attempt: "6f7a8b9c-0d1e-4f2a-9b3c-4d5e6f7a8b9c",
  sourceRun: "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d",
  attemptOf: "8b9c0d1e-2f3a-4b4c-9d5e-6f7a8b9c0d1e",
  candidate: "9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e2f",
};
const ORIGIN = "http://localhost:3031";

describe("SIM-E2E-043's stubbed responses match the agent-fix-runs contracts", () => {
  it("the attempt's Epoch read, a coding agent's attempt with no winner", () => {
    const read = SimulateApiAgentFixRunsReadResponse.parse(
      asContract(attemptEpoch(ids)),
    );
    expect(read.request.outside_candidate?.number).toBe(2);
    expect(read.report?.winner_candidate_id).toBeNull();
  });

  it("the attempt's verdict read", () => {
    const verdict = SimulateApiAgentFixRunsVerdictResponse.parse(
      attemptVerdict(ids),
    );
    expect(verdict.ending).toBe("no_improvement");
    expect(verdict.failure).toBeNull();
  });

  it("an open coding-agent brief for the attempt's run", () => {
    const brief = SimulateApiAgentFixRunsBriefResponse.parse(
      asContract(codingAgentBrief(ids)),
    );
    expect(brief.blocked).toBeNull();
    expect(brief.source_run.id).toBe(ids.sourceRun);
  });
});

// The words the flow asserts, from the page's own helpers: if the page's wording
// changes, this fails before the flow does.
describe("SIM-E2E-043's expected words are the page's", () => {
  const fix = attemptEpoch(ids) as unknown as AgentFixRunDetailApi;
  const verdict = attemptVerdict(ids) as unknown as AgentFixAttemptVerdictApi;

  it("each check row, as CheckLine renders attemptChecks", () => {
    const rows = attemptChecks(verdict, fix.request).map((c) => ({
      check:
        c.status === "skipped"
          ? c.notRun
            ? "not-run"
            : "unmeasured"
          : c.status,
      label: c.label,
      says: c.notRun
        ? `Not run: an earlier check failed (${c.notRunBecause})`
        : c.detail,
    }));
    expect(rows).toEqual(CHECK_ROWS);
  });

  it("the ending's sentence", () => {
    expect(endingSentence(fix)).toBe(ENDING_SENTENCE);
  });

  it("the copied prompt: the setup, then the brief's instructions exactly", () => {
    expect(
      codingAgentPrompt(
        { instructions: instructions(ids) },
        `${ORIGIN}/dashboard/settings/mcp-server`,
      ),
    ).toBe(copiedPrompt(ids, ORIGIN));
  });
});
