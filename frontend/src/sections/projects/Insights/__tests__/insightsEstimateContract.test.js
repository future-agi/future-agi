import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiPath } from "src/api/contracts/api-surface";
import {
  validateContractedRequestConfig,
  validateContractedResponse,
} from "src/api/contracts/openapi-contract";

const http = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", async () => {
  const { apiPath: path } = await import("src/api/contracts/api-surface");
  return {
    default: { post: (...args) => http.post(...args) },
    readQuery: vi.fn(),
    endpoints: {
      project: { insightsEstimate: () => path("/tracer/insights/estimate/") },
    },
  };
});

import { fetchScopeCount } from "../insightsApi";

const URL = apiPath("/tracer/insights/estimate/");
const PROJECT = "1b9823aa-866c-400e-b60e-d99ae498d72a";
// The server's answer for rows handed over by id: sized, not counted.
const PRICED = {
  population: 12,
  population_is_lower_bound: false,
  sample_size: 12,
  window: null,
  sample_ids: [],
  credits_estimate: 0.4,
  credit_limited: false,
  credits_left: null,
  credit_max_sample: null,
  credit_message: null,
};

const sentBody = () => http.post.mock.calls.at(-1)[1];

describe("the Insights estimate against the API contract", () => {
  beforeEach(() => {
    http.post.mockReset();
    http.post.mockResolvedValue({ data: { status: true, result: PRICED } });
  });

  it("sends a handed-over population's size, not a scope", async () => {
    const count = await fetchScopeCount({
      projectId: PROJECT,
      unit: "sessions",
      populationSize: 12,
      sampleSize: 300,
    });
    expect(sentBody()).toEqual({
      project_id: PROJECT,
      unit: "sessions",
      population_size: 12,
      sample_size: 300,
    });
    const request = validateContractedRequestConfig({
      url: URL,
      method: "post",
      data: sentBody(),
    });
    expect(request.skipped).toBeUndefined();
    expect(request.error).toBeUndefined();
    expect(request.ok).toBe(true);
    expect(count).toMatchObject({
      total: 12,
      totalIsLowerBound: false,
      credits: { estimate: 0.4, limited: false },
    });
  });

  it("still sends a scope when there is one", async () => {
    const scope = { filters: [], window: ["2026-10-01", "2026-10-08"] };
    await fetchScopeCount({ projectId: PROJECT, unit: "traces", scope });
    expect(sentBody()).toEqual({ project_id: PROJECT, unit: "traces", scope });
    expect(
      validateContractedRequestConfig({
        url: URL,
        method: "post",
        data: sentBody(),
      }).ok,
    ).toBe(true);
  });

  it.each(["ask", "draft", "preview"])(
    "accepts an action-specific %s estimate request",
    (action) => {
      const request = validateContractedRequestConfig({
        url: URL,
        method: "post",
        data: { project_id: PROJECT, action },
      });
      expect(request.skipped).toBeUndefined();
      expect(request.error).toBeUndefined();
      expect(request.ok).toBe(true);
    },
  );

  it("preserves a retryable credit check in a successful HTTP response", async () => {
    const data = {
      status: true,
      result: {
        ...PRICED,
        credit_limited: true,
        credit_check_unavailable: true,
        credit_message:
          "Credits can't be checked right now, so this wasn't started. Please retry in a moment.",
      },
    };
    http.post.mockResolvedValue({ data });
    const count = await fetchScopeCount({
      projectId: PROJECT,
      unit: "sessions",
      populationSize: 12,
    });
    expect(count.credits).toMatchObject({
      estimate: 0.4,
      limited: true,
      unavailable: true,
    });
    const response = validateContractedResponse({
      config: { url: URL, method: "post" },
      status: 200,
      data,
    });
    expect(response.skipped).toBeUndefined();
    expect(response.error).toBeUndefined();
    expect(response.ok).toBe(true);
  });

  it("accepts the answer's null window", () => {
    const response = validateContractedResponse({
      config: { url: URL, method: "post" },
      status: 200,
      data: { status: true, result: PRICED },
    });
    expect(response.skipped).toBeUndefined();
    expect(response.error).toBeUndefined();
    expect(response.ok).toBe(true);
  });
});
