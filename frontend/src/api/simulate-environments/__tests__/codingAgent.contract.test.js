import { describe, expect, it } from "vitest";

import { validateContractedResponse } from "src/api/contracts/openapi-contract";
import { OPENAPI_CONTRACT } from "src/api/contracts/openapi-contract.generated";

import {
  ATTEMPT_ID,
  SOURCE_RUN_ID,
  fixtureBlockedBrief,
  fixtureBrief,
  fixtureVerdict,
} from "./fixtures/codingAgent";

const BASE = "/simulate/api/agent-fix-runs";

const check = (url, data) =>
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

  it("keeps the coding-agent setting typed and default-off on capabilities", () => {
    expect(
      OPENAPI_CONTRACT.definitions.CapabilitiesResponse.properties
        .coding_agent_attempts_enabled,
    ).toMatchObject({ type: "boolean", default: false });
  });
});
