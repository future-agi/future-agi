import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import { MockAgGrid, renderPage } from "./__tests__/helpers";
import GuardrailManagementSection from "./GuardrailManagementSection";

const { get, navigate, params } = vi.hoisted(() => ({
  get: vi.fn(),
  navigate: vi.fn(),
  params: { tab: undefined },
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => params,
  useNavigate: () => navigate,
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get } };
});
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gw-1", isLoading: false }),
}));
vi.mock("./rules/RulesView", () => ({ default: () => <div>rules view</div> }));
vi.mock("./test/GuardrailTestTab", () => ({
  default: () => <div>test tab</div>,
}));
vi.mock("./logs/TriggeredRequestsTab", () => ({
  default: () => <div>logs tab</div>,
}));
vi.mock("./GuardrailAnalyticsTab", () => ({
  default: () => <div>analytics tab</div>,
}));
vi.mock("./FeedbackSummaryCard", () => ({
  default: () => <div>feedback tab</div>,
}));

describe("Guardrails page", () => {
  beforeEach(() => {
    navigate.mockReset();
    params.tab = undefined;
  });

  it("is one Future AGI page: an 18px semibold title and one rules view, no separate overview", async () => {
    renderPage(<GuardrailManagementSection />);
    const title = screen.getByRole("heading", { level: 1, name: "Guardrails" });
    expect(window.getComputedStyle(title).fontSize).toBe("0.9375rem");
    expect(window.getComputedStyle(title).fontWeight).toBe("600");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Rules",
      "Analytics",
      "Feedback",
      "Test",
      "Logs",
    ]);
    expect(screen.getByText("rules view")).toBeInTheDocument();
    // A check on the request reads it on every route that sends one (the
    // Rules view lists them), so the page names no subset of requests.
    expect(
      screen.getByText(
        "Checks on the requests through this gateway, before the model is called, and on the model's answer.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/chat and completion requests/)).toBeNull();
    await waitFor(() => expect(document.title).toBe("Guardrails"));
  });

  it.each([
    ["configuration", "Rules", "rules view"],
    ["overview", "Rules", "rules view"],
    ["analytics", "Analytics", "analytics tab"],
    ["feedback", "Feedback", "feedback tab"],
    ["playground", "Test", "test tab"],
    ["logs", "Logs", "logs tab"],
  ])("keeps the /%s link working", (slug, tab, content) => {
    params.tab = slug;
    renderPage(<GuardrailManagementSection />);
    expect(screen.getByRole("tab", { name: tab })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText(content)).toBeInTheDocument();
  });

  it("moves between tabs by URL", async () => {
    const user = userEvent.setup();
    params.tab = "logs";
    renderPage(<GuardrailManagementSection />);
    await user.click(screen.getByRole("tab", { name: "Test" }));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/gateway/guardrails/playground",
      { replace: true },
    );
    await user.click(screen.getByRole("tab", { name: "Rules" }));
    expect(navigate).toHaveBeenLastCalledWith("/dashboard/gateway/guardrails", {
      replace: true,
    });
  });
});

// The Logs view, not mocked: outside production builds the axios contract
// guard refuses a query param the request-logs contract does not declare
// (gateway_id) before the request is sent, so the list stayed empty. The
// view sends only declared filters, and a blocked request (the gateway's
// 403, recorded with guardrail_action "blocked") reads as an error.
describe("Guardrails page > Logs view", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({
      data: {
        count: 1,
        results: [
          {
            id: "log-1",
            request_id: "req-blocked-1",
            started_at: "2026-10-28T19:46:17Z",
            model: "gpt-4o-mini",
            status_code: 403,
            guardrail_triggered: true,
            error_message: "Request blocked by guardrail: keyword-blocklist",
            metadata: {
              guardrail_name: "keyword-blocklist",
              guardrail_action: "blocked",
            },
          },
        ],
      },
    });
  });

  it("lists guardrail-triggered requests with a query the API contract accepts", async () => {
    const { default: TriggeredRequestsTab, ACTIONS } = await vi.importActual(
      "./logs/TriggeredRequestsTab",
    );
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);

    const row = await screen.findByTestId("row-log-1");
    expect(within(row).getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(within(row).getByText("403")).toBeInTheDocument();
    expect(within(row).getByText("Blocked")).toBeInTheDocument();
    expect(ACTIONS.blocked.tone).toBe("error");

    const [url, config] = get.mock.calls[0];
    expect(config.params).toMatchObject({ guardrail_triggered: true });
    expect(config.params).not.toHaveProperty("gateway_id");
    const checked = validateContractedRequestConfig({
      url,
      method: "get",
      ...config,
    });
    expect(checked).toMatchObject({ ok: true });
    expect(checked.skipped).toBeUndefined();
    // The guard this pins: the undeclared gateway_id is refused.
    expect(
      validateContractedRequestConfig({
        url,
        method: "get",
        params: { ...config.params, gateway_id: "gw-1" },
      }).ok,
    ).toBe(false);
  });
});
