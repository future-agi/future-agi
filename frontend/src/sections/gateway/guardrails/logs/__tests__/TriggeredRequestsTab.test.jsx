import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MockAgGrid, renderPage } from "../../__tests__/helpers";
import TriggeredRequestsTab, { ACTIONS } from "../TriggeredRequestsTab";

const { get, navigate } = vi.hoisted(() => ({
  get: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get } };
});
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => navigate,
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

// Two of the demo org's matched requests (GET request-logs/, trimmed).
const LOGS = {
  count: 43,
  results: [
    {
      id: "029f80a9",
      request_id: "01M5CAC8ZNP41AWX06CPRG4HJM",
      model: "gpt-4o-mini",
      started_at: "2026-10-20T09:30:45.621248Z",
      status_code: 403,
      error_message:
        "Request blocked by guardrail: keyword-blocklist — Blocked keywords found: internal only",
      guardrail_triggered: true,
      metadata: {
        guardrail_name: "keyword-blocklist",
        guardrail_action: "blocked",
        auth_key_name: "support-bot",
      },
    },
    {
      id: "075de131",
      request_id: "01M5CAC5KBKFVV6DSQNW819JCX",
      model: "gpt-4o-mini",
      started_at: "2026-10-20T09:30:41.000000Z",
      status_code: 200,
      error_message: "",
      guardrail_triggered: true,
      metadata: {
        guardrail_name: "prompt-injection",
        guardrail_action: "warned",
        guardrail_warnings:
          "prompt-injection: Potential prompt injection detected: instruction_override (score: 0.83)",
        auth_key_name: "triage",
      },
    },
  ],
};

describe("Guardrails > Logs", () => {
  beforeEach(() => {
    get.mockReset();
    navigate.mockReset();
    get.mockResolvedValue({ data: LOGS });
  });

  it("reads the matched requests without the gateway_id the request-logs contract refuses", async () => {
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    await screen.findByTestId("row-029f80a9");
    const [url, config] = get.mock.calls[0];
    expect(url).toBe("/agentcc/request-logs/");
    expect(config.params).toEqual({
      guardrail_triggered: true,
      limit: 100,
      page: 1,
      ordering: "-started_at",
    });
    expect(screen.getByText(/43 requests/)).toBeInTheDocument();
  });

  it("shows the rule, what it did and why, for blocks and warnings alike", async () => {
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    const blocked = await screen.findByTestId("row-029f80a9");
    // The rule by its catalog name, as on Rules, Analytics and Feedback.
    expect(within(blocked).getByText("Keyword Blocklist")).toBeInTheDocument();
    expect(within(blocked).queryByText("keyword-blocklist")).toBeNull();
    expect(within(blocked).getByText("Blocked")).toBeInTheDocument();
    expect(within(blocked).getByText("403")).toBeInTheDocument();
    expect(
      within(blocked).getByText("Blocked keywords found: internal only"),
    ).toBeInTheDocument();
    const warned = screen.getByTestId("row-075de131");
    expect(within(warned).getByText("Warned")).toBeInTheDocument();
    expect(
      within(warned).getByText(/Potential prompt injection detected/),
    ).toBeInTheDocument();
  });

  // Each row says what its checks did, coloured by it: the action the
  // gateway recorded, else the strongest action of the checks stored with
  // the request (block, then warn, then log). A block is a block whatever
  // the stage: the gateway refuses a blocked answer with a 403.
  it("shows each request's action, from the gateway's record or its checks, with an Action filter", async () => {
    const user = userEvent.setup();
    get.mockResolvedValue({
      data: {
        count: 4,
        results: [
          ...LOGS.results,
          {
            id: "c-block",
            request_id: "req-block",
            status_code: 403,
            guardrail_triggered: true,
            guardrail_results: [
              { name: "pii-detection", action: "warn" },
              { name: "secret-detection", action: "block" },
            ],
          },
          {
            id: "c-log",
            request_id: "req-log",
            status_code: 200,
            guardrail_triggered: true,
            guardrail_results: [{ name: "topic-restriction", action: "log" }],
          },
        ],
      },
    });
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    const blocked = await screen.findByTestId("row-c-block");
    expect(within(blocked).getByText("Blocked")).toBeInTheDocument();
    expect(within(blocked).getByText("403")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("row-c-log")).getByText("Logged"),
    ).toBeInTheDocument();
    expect(ACTIONS.blocked.tone).toBe("error");
    expect(ACTIONS.warned.tone).toBe("warning");
    expect(screen.queryByText(/not enforced/i)).toBeNull();

    const action = screen.getByRole("combobox", { name: "Action" });
    await user.click(action);
    await user.click(await screen.findByRole("option", { name: "Logged" }));
    expect(screen.getByTestId("row-c-log")).toBeInTheDocument();
    expect(screen.queryByTestId("row-c-block")).toBeNull();
    await user.click(action);
    await user.click(await screen.findByRole("option", { name: "Blocked" }));
    expect(screen.getByTestId("row-c-block")).toBeInTheDocument();
    expect(screen.getByTestId("row-029f80a9")).toBeInTheDocument();
    expect(screen.queryByTestId("row-c-log")).toBeNull();
  });

  it("opens filtered to one rule from a rule's drawer link", async () => {
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />, {
      route: "/dashboard/gateway/guardrails/logs?rule=prompt-injection",
    });
    await screen.findByTestId("row-075de131");
    expect(screen.queryByTestId("row-029f80a9")).toBeNull();
  });

  it("shows the rule from a drawer link while the requests are still loading", async () => {
    get.mockReturnValue(new Promise(() => {}));
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />, {
      route: "/dashboard/gateway/guardrails/logs?rule=prompt-injection",
    });
    expect(await screen.findByTestId("grid-loading")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Rule" })).toHaveTextContent(
      "Prompt Injection",
    );
  });

  it("filters and searches by the rule's name, keeping its id for the link", async () => {
    const user = userEvent.setup();
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    await screen.findByTestId("row-029f80a9");
    await user.click(screen.getByRole("combobox", { name: "Rule" }));
    const options = (await screen.findAllByRole("option")).map(
      (option) => option.textContent,
    );
    expect(options).toEqual([
      "All rules",
      "Keyword Blocklist",
      "Prompt Injection",
    ]);
    await user.click(screen.getByRole("option", { name: "Prompt Injection" }));
    expect(screen.queryByTestId("row-029f80a9")).toBeNull();
    expect(screen.getByTestId("row-075de131")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Rule" }));
    await user.click(screen.getByRole("option", { name: "All rules" }));
    await user.type(
      screen.getByPlaceholderText("Search rule, reason, model, request id"),
      "keyword block",
    );
    expect(screen.getByTestId("row-029f80a9")).toBeInTheDocument();
    expect(screen.queryByTestId("row-075de131")).toBeNull();
  });

  it("sets the request id in an FA type size, not a hard-coded one", async () => {
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    const id = await screen.findByText("01M5CAC8ZNP41AWX06CPRG4HJM");
    const style = window.getComputedStyle(id);
    expect(style.fontFamily).toMatch(/Fira Code.*, monospace$/);
    expect(style.fontSize).toBe("0.6875rem");
  });

  it("opens a row in the request logs", async () => {
    const user = userEvent.setup();
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    await user.click(await screen.findByText("Keyword Blocklist"));
    expect(navigate).toHaveBeenCalledWith(
      "/dashboard/gateway/logs?request_id=01M5CAC8ZNP41AWX06CPRG4HJM",
    );
  });

  it("has one empty state, and an error state that is not an empty one", async () => {
    get.mockResolvedValueOnce({ data: { count: 0, results: [] } });
    const { unmount } = renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("No request has matched a rule yet"),
    ).toBeInTheDocument();
    unmount();

    get.mockRejectedValueOnce(new Error("boom"));
    renderPage(<TriggeredRequestsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("Matched requests could not be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("No request has matched a rule yet")).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument(),
    );
  });
});
