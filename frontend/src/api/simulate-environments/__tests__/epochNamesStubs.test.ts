import { describe, expect, it } from "vitest";

import {
  SimulateApiAgentFixRunsReadResponse,
  SimulateV3TestExecutionCallsResponse,
} from "src/generated/api-contracts/api.zod";

import {
  CHANGES,
  ENV_ID,
  NAMED,
  NAMED_KEY,
  UNNAMED_KEY,
  checkRunCalls,
  readyEpoch,
  type Ids,
} from "../../../../../e2e/flows/simulate/epoch-change-and-scenario-names.payload";

// SIM-E2E-041 serves these from the browser, because the E2E stack cannot hold an
// epoch or its check runs. Parsing them with the generated contract keeps them true
// to the API: a field the API renames, drops or makes nullable fails here.

// The stubs use the prebuilt template's id for the environment, the only one the
// E2E stack opens an epoch page under; the contract's ids are UUIDs.
const ENV_UUID = "5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b";
const asContract = (stub: unknown): unknown =>
  JSON.parse(JSON.stringify(stub).replaceAll(`"${ENV_ID}"`, `"${ENV_UUID}"`));

const ids: Ids = {
  epoch: "6f7a8b9c-0d1e-4f2a-9b3c-4d5e6f7a8b9c",
  candidate: "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d",
  baseRun: "8b9c0d1e-2f3a-4b4c-9d5e-6f7a8b9c0d1e",
  baseExecution: "9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e2f",
  candidateRun: "0d1e2f3a-4b5c-4d6e-9f7a-8b9c0d1e2f3a",
  candidateExecution: "1e2f3a4b-5c6d-4e7f-8a8b-9c0d1e2f3a4b",
};

describe("SIM-E2E-041's stubbed responses match the agent-fix-runs and calls contracts", () => {
  it("the ready epoch's read, with its change line and stored scenario names", () => {
    const read = SimulateApiAgentFixRunsReadResponse.parse(
      asContract(readyEpoch(ids)),
    );
    expect(read.candidates[0]?.wording?.changes).toBe(CHANGES);
    expect(read.scenario_names).toEqual({ [NAMED_KEY]: NAMED });
    expect(read.scenario_names).not.toHaveProperty(UNNAMED_KEY);
  });

  it.each([ids.baseExecution, ids.candidateExecution])(
    "check run %s's calls",
    (executionId: string) => {
      expect(() =>
        SimulateV3TestExecutionCallsResponse.parse(
          checkRunCalls(ids, executionId),
        ),
      ).not.toThrow();
    },
  );
});
