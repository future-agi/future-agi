import React, { forwardRef, useEffect } from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import { catalogEntry } from "./catalog";
import { useRuleActions } from "./hooks/useRuleActions";
import { MockAgGrid, renderPage } from "./__tests__/helpers";
import RulesView from "./rules/RulesView";
import GuardrailTestTab from "./test/GuardrailTestTab";

// Saving creates a whole new active org-config version only through POST
// org-configs/, and a section left out of that payload is reset to its
// default. The Guardrails page never saves that way: it switches and edits a
// check with toggle-guardrail and update-guardrail, and removes a check or
// sets the timeout with update-config, which deep-merges only the sections a
// patch sends. So no guardrails save can reset budgets, rate limits, the IP
// ACL, alerting, privacy, tool policy, MCP, A2A, audit or the model sections.

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
let orgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel }),
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

const SAVED_GUARDRAILS = {
  checks: {
    "pii-detection": {
      enabled: true,
      action: "block",
      confidence_threshold: 0.5,
    },
  },
};

function wrapper({ children }) {
  const client = new QueryClient({
    mutationCache: new MutationCache({ onError: () => {} }),
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
wrapper.propTypes = { children: PropTypes.node };

describe("GuardrailManagementSection Save & Activate", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({
      data: { result: { version: 8, gateway_synced: true } },
    });
  });

  it("keeps every other org-config section when saving guardrails", async () => {
    const { result } = renderHook(
      () =>
        useRuleActions({ gatewayId: "gateway-1", saved: SAVED_GUARDRAILS }),
      { wrapper },
    );
    const row = {
      id: "pii-detection",
      ruleName: "pii-detection",
      label: "PII Detection",
    };

    await act(() =>
      result.current.saveRule({
        catalogName: "pii-detection",
        meta: catalogEntry("pii-detection"),
        check: SAVED_GUARDRAILS.checks["pii-detection"],
        form: { enabled: true, action: "block", threshold: 0.8, fields: {} },
      }),
    );
    await act(() => result.current.toggleRule(row, false));
    await act(() => result.current.saveTimeout(3000));
    await act(() => result.current.removeRule(row));

    const calls = post.mock.calls.map(([url, body]) => [url, body]);
    expect(calls.map(([url]) => url)).toEqual([
      "/agentcc/gateways/gateway-1/update-guardrail/",
      "/agentcc/gateways/gateway-1/toggle-guardrail/",
      "/agentcc/gateways/gateway-1/update-config/",
      "/agentcc/gateways/gateway-1/update-config/",
    ]);
    // update-guardrail and toggle-guardrail name one check and change it.
    expect(calls[0][1]).toEqual({
      name: "pii-detection",
      config: { confidence_threshold: 0.8 },
    });
    expect(calls[1][1]).toEqual({ name: "pii-detection", enabled: false });
    // update-config gets a guardrails-only patch; every other section stays
    // as stored.
    for (const [, body] of calls.slice(2)) {
      expect(Object.keys(body)).toEqual(["guardrails"]);
    }
    expect(calls[2][1]).toEqual({ guardrails: { timeout_ms: 3000 } });
  });
});

const RULES_PATH = "/dashboard/gateway/guardrails";
const TEST_PATH = `${RULES_PATH}/playground`;
let visits;
let guardrails;
let pipelineOff;

function Journey() {
  const location = useLocation();
  useEffect(() => { visits.push(location); }, [location]);
  return (
    <>
      <output aria-label="Current page">{location.pathname}</output>
      <Routes>
        <Route path={RULES_PATH} element={<RulesView gatewayId="gateway-1" />} />
        <Route path={TEST_PATH} element={<GuardrailTestTab gatewayId="gateway-1" />} />
      </Routes>
    </>
  );
}

async function openPii(user, creating = false) {
  if (creating) {
    await screen.findByText("No guardrails");
    await user.click(screen.getByRole("button", { name: "Add check" }));
    const catalog = await screen.findByRole("dialog", { name: "Add a check" });
    await user.click(within(catalog).getByText("PII Detection"));
  } else {
    const row = await screen.findByTestId("row-pii-detection");
    await user.click(within(row).getByText("PII Detection"));
  }
  return screen.findByRole("dialog", { name: "PII Detection" });
}

async function chooseWarn(user, drawer) {
  await user.click(within(drawer).getByRole("combobox", { name: /^Action/ }));
  await user.click(await screen.findByRole("option", { name: /^Warn/ }));
}

const testPosts = () => post.mock.calls.filter(([url]) => url.endsWith("/test-playground/"));

describe("Save & try a represented guardrail", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    visits = [];
    orgLevel = 8;
    pipelineOff = false;
    guardrails = structuredClone(SAVED_GUARDRAILS);
    get.mockImplementation((url) => {
      if (url.includes("org-configs/active")) return Promise.resolve({ data: { result: { guardrails } } });
      if (url.endsWith("/config/")) return Promise.resolve({ data: { result: { guardrail_pipeline: { enabled: !pipelineOff } } } });
      if (url.endsWith("/providers/")) return Promise.resolve({ data: { result: { providers: [{ id: "own", models: ["gpt-4o-mini"], pushed: true }] } } });
      if (url.includes("guardrail-feedback/summary")) return Promise.resolve({ data: { result: [] } });
      if (url.includes("guardrail-rules")) return Promise.resolve({ data: { result: { rules: [] } } });
      if (url.includes("guardrail-overview")) return Promise.resolve({ data: { result: {} } });
      throw new Error(`Unexpected read: ${url}`);
    });
    post.mockResolvedValue({ data: { result: { version: 8, gateway_synced: true } } });
  });

  it.each(["create", "edit"])("%s saves once and carries the check to its existing sample without running", async (mode) => {
    const creating = mode === "create";
    if (creating) guardrails = {};
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    const drawer = await openPii(user, creating);
    await chooseWarn(user, drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save & try" }));
    expect(await screen.findByText("Try PII Detection")).toBeInTheDocument();
    expect(screen.getByLabelText("Current page")).toHaveTextContent(TEST_PATH);
    expect(visits.some((location) => location.pathname === TEST_PATH && location.state?.guardrailTest?.checkName === "pii-detection" && location.state.guardrailTest.gatewayId === "gateway-1")).toBe(true);
    expect(screen.getByLabelText(/^Prompt/)).toHaveValue("My name is John Smith, SSN 123-45-6789, email john@example.com");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/agentcc/gateways/gateway-1/update-guardrail/",
      expect.objectContaining({ name: "pii-detection", config: expect.objectContaining({ action: "warn" }) }),
      expect.anything(),
    );
    expect(testPosts()).toHaveLength(0);
  });

  it.each([
    ["not applied", { version: 8, gateway_synced: false, gateway_warning: "Gateway is unreachable" }, /Saved, but the gateway did not apply it: Gateway is unreachable/],
    ["unknown apply", { version: 8 }, /Gateway apply was not confirmed/],
    ["missing receipt", undefined, /Gateway apply was not confirmed/],
  ])("keeps the form and does not navigate when save is %s", async (_, receipt, message) => {
    post.mockResolvedValue({ data: { result: receipt } });
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    const drawer = await openPii(user);
    await chooseWarn(user, drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save & try" }));
    expect(await within(drawer).findByText(message)).toBeInTheDocument();
    expect(within(drawer).getByRole("combobox", { name: /^Action/ })).toHaveTextContent("Warn");
    expect(screen.getByLabelText("Current page").textContent).toBe(RULES_PATH);
    expect(visits.some((location) => location.pathname === TEST_PATH)).toBe(false);
    expect(post).toHaveBeenCalledTimes(1);
    expect(testPosts()).toHaveLength(0);
  });

  it.each([
    ["validation", { statusCode: 400, result: "The action is invalid." }, /The action is invalid/],
    ["network", { code: "ERR_NETWORK", message: "Connection interrupted" }, /Connection interrupted|Could not save/],
  ])("retains form values after a %s failure", async (_, error, message) => {
    post.mockRejectedValue(error);
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    const drawer = await openPii(user);
    await chooseWarn(user, drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save & try" }));
    expect(await within(drawer).findByText(message)).toBeInTheDocument();
    expect(within(drawer).getByRole("combobox", { name: /^Action/ })).toHaveTextContent("Warn");
    expect(within(drawer).getByRole("button", { name: "Save & try" })).toBeEnabled();
    expect(screen.getByLabelText("Current page").textContent).toBe(RULES_PATH);
    expect(post).toHaveBeenCalledTimes(1);
    expect(testPosts()).toHaveLength(0);
  });

  it("does not turn a no-change save into applied evidence", async () => {
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    const drawer = await openPii(user);
    await user.click(within(drawer).getByRole("button", { name: "Save & try" }));
    expect(await within(drawer).findByText(/Gateway apply was not confirmed/)).toBeInTheDocument();
    expect(screen.getByLabelText("Current page").textContent).toBe(RULES_PATH);
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps ordinary Save without navigating or dispatching", async () => {
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    const drawer = await openPii(user);
    await chooseWarn(user, drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "PII Detection" })).toBeNull());
    expect(screen.getByLabelText("Current page").textContent).toBe(RULES_PATH);
    expect(post).toHaveBeenCalledTimes(1);
    expect(testPosts()).toHaveLength(0);
  });

  it.each([
    ["guardrails off", 8, true],
    ["read-only role", 1, false],
  ])("disables Save & try with %s", async (_, level, off) => {
    orgLevel = level;
    pipelineOff = off;
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    const drawer = await openPii(user);
    expect(within(drawer).getByRole("button", { name: "Save & try" })).toBeDisabled();
    if (off) expect(within(drawer).getByRole("button", { name: "Save", exact: true })).toBeEnabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("does not offer a fabricated sample for an unrepresented check", async () => {
    guardrails = { checks: { "keyword-blocklist": { enabled: true, action: "block", config: { words: ["internal only"] } } } };
    const user = userEvent.setup();
    renderPage(<Journey />, { route: RULES_PATH });
    await user.click(within(await screen.findByTestId("row-keyword-blocklist")).getByText("Keyword Blocklist"));
    const drawer = await screen.findByRole("dialog", { name: "Keyword Blocklist" });
    expect(within(drawer).queryByRole("button", { name: "Save & try" })).toBeNull();
    expect(within(drawer).getByRole("button", { name: "Save", exact: true })).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
});
