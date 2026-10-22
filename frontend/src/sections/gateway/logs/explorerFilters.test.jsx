import React from "react";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import PropTypes from "prop-types";
import { buildFilterParams } from "./hooks/useRequestLogs";
import useFilters from "./hooks/useFilters";
import {
  AGENT_BILLING_FILTER_PARAMS,
  EMPTY_AGENT_BILLING_FILTERS,
  agentBillingParams,
  readAgentBillingFilters,
  writeAgentBillingFilters,
} from "./explorerFilters";

const V2_FILTERS = {
  billingMode: "managed",
  runId: "run-7",
  agentId: "planner",
  step: "plan,act",
  policyVariant: "candidate",
  errorClass: "rate_limited,timeout",
};

const V2_PARAMS = {
  billing_mode: "managed",
  run_id: "run-7",
  agent_id: "planner",
  step: "plan,act",
  policy_variant: "candidate",
  error_class: "rate_limited,timeout",
};

describe("agent and billing filters", () => {
  it("name the six request-log query params", () => {
    expect(Object.values(AGENT_BILLING_FILTER_PARAMS).sort()).toEqual(
      Object.keys(V2_PARAMS).sort(),
    );
  });

  it("serialize into the list API's query params", () => {
    expect(buildFilterParams({ ...V2_FILTERS, model: "gpt-5" })).toEqual({
      ...V2_PARAMS,
      model: "gpt-5",
    });
  });

  it("serialize into the export's query params, and only those", () => {
    expect(agentBillingParams({ ...V2_FILTERS, model: "gpt-5" })).toEqual(
      V2_PARAMS,
    );
    expect(agentBillingParams({ billingMode: "", step: undefined })).toEqual(
      {},
    );
  });

  it("read the panel's values from the URL filters and write them back", () => {
    const local = readAgentBillingFilters(V2_FILTERS);
    expect(local).toEqual({
      billingMode: "managed",
      runId: "run-7",
      agentId: "planner",
      step: ["plan", "act"],
      policyVariant: "candidate",
      errorClass: ["rate_limited", "timeout"],
    });
    expect(writeAgentBillingFilters(local)).toEqual(V2_FILTERS);
  });

  it("write nothing for empty values and trim typed ids", () => {
    expect(readAgentBillingFilters({})).toEqual(EMPTY_AGENT_BILLING_FILTERS);
    expect(writeAgentBillingFilters(EMPTY_AGENT_BILLING_FILTERS)).toEqual({});
    expect(
      writeAgentBillingFilters({
        ...EMPTY_AGENT_BILLING_FILTERS,
        runId: "  run-9 ",
        agentId: "   ",
      }),
    ).toEqual({ runId: "run-9" });
  });
});

describe("useFilters with the agent and billing filters", () => {
  function wrapperAt(url) {
    const Wrapper = ({ children }) => (
      <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>
    );
    Wrapper.propTypes = { children: PropTypes.node };
    return Wrapper;
  }

  const useFiltersAndSearch = () => ({
    ...useFilters(),
    search: useLocation().search,
  });

  it("writes them to the URL in snake_case", () => {
    const { result } = renderHook(useFiltersAndSearch, {
      wrapper: wrapperAt("/dashboard/gateway/logs"),
    });
    act(() => result.current.setFilters(V2_FILTERS));
    expect(
      Object.fromEntries(new URLSearchParams(result.current.search)),
    ).toEqual(V2_PARAMS);
    expect(result.current.activeFilterCount).toBe(6);
  });

  it("reads them back from a shared URL in camelCase", () => {
    const { result } = renderHook(useFiltersAndSearch, {
      wrapper: wrapperAt(
        `/dashboard/gateway/logs?${new URLSearchParams(V2_PARAMS)}`,
      ),
    });
    expect(result.current.filters).toEqual(V2_FILTERS);
  });
});
