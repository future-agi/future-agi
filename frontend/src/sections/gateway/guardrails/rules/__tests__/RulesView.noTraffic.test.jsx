// On a fresh organization (no requests in the window) the Matched figure
// shows its count alone: a rate of no requests is not "0.0%" beside it.
import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import {
  DEMO_FEEDBACK_SUMMARY,
  DEMO_GUARDRAILS,
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  renderPage,
} from "../../__tests__/helpers";
import RulesView from "../RulesView";

const { get, post, snack } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  snack: vi.fn(),
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: snack }));
// Rule writes are Owner or Admin only (useGatewayAdmin); these cases act as
// an Admin unless they set another level.
let orgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

let guardrails;
let ruleStats;
let orgConfigFails;
let nodePipeline;
let overview;

function answer(url) {
  if (url.endsWith("/gateways/gw-1/config/")) {
    return Promise.resolve({
      data: { result: { guardrail_pipeline: nodePipeline } },
    });
  }
  if (url.includes("org-configs/active")) {
    if (orgConfigFails) return Promise.reject(new Error("boom"));
    return Promise.resolve({ data: { result: { guardrails } } });
  }
  if (url.includes("guardrail-rules")) {
    return Promise.resolve({ data: { result: ruleStats } });
  }
  if (url.includes("guardrail-overview")) {
    return Promise.resolve({ data: { result: overview } });
  }
  if (url.includes("guardrail-feedback/summary")) {
    return Promise.resolve({ data: { result: DEMO_FEEDBACK_SUMMARY } });
  }
  return Promise.reject(new Error(`unexpected GET ${url}`));
}

const renderRules = () => renderPage(<RulesView gatewayId="gw-1" />);

describe("Guardrails overview with no traffic", () => {
  beforeEach(() => {
    guardrails = structuredClone(DEMO_GUARDRAILS);
    ruleStats = structuredClone(DEMO_RULE_STATS);
    orgConfigFails = false;
    orgLevel = 8;
    nodePipeline = { enabled: true, fail_open: false, mode: "sequential" };
    get.mockReset();
    post.mockReset();
    snack.mockReset();
    get.mockImplementation(answer);
  });

  it("shows Matched as 0 with no rate when there were no requests", async () => {
    overview = {
      ...DEMO_OVERVIEW,
      total_requests: 0,
      guardrail_triggered: 0,
      trigger_rate: 0,
      block_count: 0,
      warn_count: 0,
      block_rate: 0,
      warn_rate: 0,
    };
    renderRules();
    const strip = await screen.findByRole("group", { name: "Last 7 days" });
    const matched = await within(strip).findByText("Matched");
    expect(matched.nextElementSibling.textContent).toBe("0");
  });

  it("keeps the rate beside the count when there were requests", async () => {
    overview = structuredClone(DEMO_OVERVIEW);
    renderRules();
    const strip = await screen.findByRole("group", { name: "Last 7 days" });
    const matched = await within(strip).findByText("Matched");
    expect(matched.nextElementSibling.textContent).toMatch(/^43\s?7\.3%$/);
  });
});
