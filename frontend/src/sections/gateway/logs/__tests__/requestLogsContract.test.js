import { describe, expect, it } from "vitest";

import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";

import { AGENT_BILLING_FILTER_PARAMS } from "../explorerFilters";

// GET /agentcc/request-logs/ honours the log entry v2 filters
// (agentcc/views/request_log.py V2_COLUMN_FILTERS and error_class, declared
// on AgentccRequestLogQuerySerializer). A dev build enforces the request
// contract, so a filter missing from it fails Run detail and the Logs filters
// with "query contract validation failed".
const listQuery = (extra) =>
  validateContractedRequestConfig({
    url: "/agentcc/request-logs/",
    method: "get",
    params: { page: 1, limit: 25, ordering: "-started_at", ...extra },
  });

describe("request-logs query contract", () => {
  it("is enforced for the list (so the checks below mean something)", () => {
    const result = listQuery({ not_a_filter: "x" });
    expect(result.ok).toBe(false);
  });

  it("accepts what Run detail sends", () => {
    const result = validateContractedRequestConfig({
      url: "/agentcc/request-logs/",
      method: "get",
      params: {
        run_id: "run-support-009",
        page: 1,
        limit: 500,
        ordering: "started_at",
      },
    });
    expect(result).toMatchObject({ ok: true });
  });

  it.each([
    ...Object.values(AGENT_BILLING_FILTER_PARAMS),
    "credential_type",
    "env",
  ])("accepts the v2 filter %s", (name) => {
    expect(listQuery({ [name]: "a,b" })).toMatchObject({ ok: true });
  });
});
