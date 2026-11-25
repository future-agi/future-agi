import { describe, expect, it } from "vitest";

import {
  SimulateApiAgentFixIssuesCreateBody,
  SimulateApiAgentFixIssuesListResponse,
  SimulateApiAgentFixIssuesPlanResponse,
  SimulateApiAgentFixIssuesReadResponse,
  SimulateApiAgentFixIssuesRunBody,
  SimulateApiAgentFixRunsReadResponse,
} from "src/generated/api-contracts/api.zod";

import {
  ENV_ID,
  RUN_BODY,
  createBody,
  issueJobDetail,
  issuePlan,
  issueStatusList,
  readyEpoch,
} from "../../../../../e2e/flows/error-feed/run-epoch-from-issue.payload";

// ERR-E2E-006 serves these from the browser, because the E2E stack cannot hold an
// issue job or an epoch. Parsing them with the generated contract keeps them true
// to the API: a field the API renames, drops or makes nullable fails here.

// The stubs use the prebuilt template's id for the environment, the only one the
// E2E stack opens an epoch page under; the contract's ids are UUIDs.
const ENV_UUID = "2c4e6f80-a1b2-4c3d-8e4f-5a6b7c8d9e0f";
const asContract = (stub) =>
  JSON.parse(JSON.stringify(stub).replaceAll(`"${ENV_ID}"`, `"${ENV_UUID}"`));

const ids = {
  cluster: "S-E6CONTRACT",
  job: "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d",
  epoch: "8b9c0d1e-2f3a-4b4c-9d5e-6f7a8b9c0d1e",
  candidate: "9c0d1e2f-3a4b-4c5d-8e6f-7a8b9c0d1e2f",
};

describe("ERR-E2E-006's stubbed responses match the agent-fix-issues contract", () => {
  it.each([true, false])("plan, with a remembered environment: %s", (env) => {
    expect(() =>
      SimulateApiAgentFixIssuesPlanResponse.parse(
        asContract(issuePlan(ids.cluster, env)),
      ),
    ).not.toThrow();
  });

  it.each(["drafting", "review", "measuring", "launched"])(
    "job detail and batch status while %s",
    (phase) => {
      expect(() =>
        SimulateApiAgentFixIssuesReadResponse.parse(
          asContract(issueJobDetail(ids, phase)),
        ),
      ).not.toThrow();
      expect(() =>
        SimulateApiAgentFixIssuesListResponse.parse(
          asContract(issueStatusList(ids, phase)),
        ),
      ).not.toThrow();
    },
  );

  it("the ready epoch's read, carrying its issue", () => {
    expect(() =>
      SimulateApiAgentFixRunsReadResponse.parse(asContract(readyEpoch(ids))),
    ).not.toThrow();
  });

  it("the request bodies the flow expects", () => {
    expect(() =>
      SimulateApiAgentFixIssuesCreateBody.parse(
        asContract(createBody(ids.cluster)),
      ),
    ).not.toThrow();
    expect(() =>
      SimulateApiAgentFixIssuesRunBody.parse(RUN_BODY),
    ).not.toThrow();
  });
});
