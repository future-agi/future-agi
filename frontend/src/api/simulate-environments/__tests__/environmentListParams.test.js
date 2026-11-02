import { describe, it, expect } from "vitest";
import {
  EMPTY_ENV_FILTERS,
  buildEnvironmentListParams,
} from "../environmentListParams";

describe("buildEnvironmentListParams", () => {
  it("sends only the 1-based page and limit with no filters", () => {
    expect(
      buildEnvironmentListParams({
        page: 0,
        pageSize: 25,
        filters: EMPTY_ENV_FILTERS,
      }),
    ).toEqual({ page: 1, limit: 25 });
  });

  it("sends every filter that is set", () => {
    expect(
      buildEnvironmentListParams({
        page: 0,
        pageSize: 25,
        filters: {
          search: "  refund ",
          status: "failed",
          agentType: "voice",
          agentId: "u1",
        },
      }),
    ).toEqual({
      page: 1,
      limit: 25,
      search: "refund",
      status: "failed",
      agentType: "voice",
      agentDefinitionId: "u1",
    });
  });

  it("drops a whitespace-only search", () => {
    const params = buildEnvironmentListParams({
      page: 1,
      pageSize: 10,
      filters: { ...EMPTY_ENV_FILTERS, search: "   " },
    });
    expect(params).toEqual({ page: 2, limit: 10 });
  });
});
