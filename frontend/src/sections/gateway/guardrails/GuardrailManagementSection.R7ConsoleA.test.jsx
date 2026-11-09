// The Guardrails Test tab says what happened. A warn is a 2xx
// with x-agentcc-guardrail-triggered: true and must not read as a pass; a
// block (403 guardrail_error) reads "Blocked (403)"; a provider 429/500 is an
// error, never green; history shows each test's real outcome; the help text
// names no 246/446, which do not exist; the Logs tab says what each
// request's checks did.
import React, { forwardRef } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MockAgGrid, renderPage } from "./__tests__/helpers";
import GuardrailTestTab from "./test/GuardrailTestTab";
import { OUTCOMES } from "./test/interpret-result";
import TriggeredRequestsTab, { ACTIONS } from "./logs/TriggeredRequestsTab";

// The test runs through the gateway's admin path (useGatewayAdmin); these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, post, get } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

const WARN = {
  status_code: 200,
  body: { choices: [{ message: { content: "ok" } }] },
  model: "gpt-4o-mini",
  blocked: false,
  warned: true,
  guardrail_action: "warn",
  triggered_checks: ["pii-detection", "secret-detection"],
  guardrail_headers: {
    "x-agentcc-guardrail-triggered": "true",
    "x-agentcc-guardrail-action": "warn",
    "x-agentcc-guardrails-triggered": "pii-detection,secret-detection",
  },
};

async function runTest(user, result, prompt = "hello") {
  post.mockResolvedValueOnce({ data: { status: true, result } });
  const box = screen.getByLabelText(/^Prompt/);
  await user.clear(box);
  await user.type(box, prompt);
  await user.click(screen.getByRole("button", { name: "Run test" }));
}

const resultCard = () => within(screen.getByRole("region", { name: "Result" }));

function historyRow(prompt) {
  const history = screen.getByRole("region", { name: "Recent tests" });
  return within(within(history).getByText(prompt).closest("tr"));
}

describe("Guardrails Test tab (NG-35)", () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
  });

  const renderTab = () =>
    renderPage(<GuardrailTestTab gatewayId="gateway-1" />);

  it("names the real codes and headers in its help text", () => {
    renderTab();

    expect(screen.queryByText(/246|446/)).toBeNull();
    const help = screen.getByText(/A block answers HTTP 403/);
    expect(help.textContent).toMatch(/guardrail_error/);
    expect(help.textContent).toMatch(/x-agentcc-guardrail-triggered: true/);
    expect(help.textContent).toMatch(/x-agentcc-guardrails-triggered/);
  });

  it("shows a warn as warned, naming the checks", async () => {
    const user = userEvent.setup();
    renderTab();
    await runTest(user, WARN);

    const card = resultCard();
    expect(await card.findByText("Warned (200)")).toBeInTheDocument();
    expect(
      card.getByText(
        "A guardrail warned and let the request through: pii-detection, secret-detection.",
      ),
    ).toBeInTheDocument();
    expect(card.queryByText(/passed all guardrails/)).toBeNull();
  });

  it("says triggered without names when the gateway sends none", async () => {
    const user = userEvent.setup();
    renderTab();
    await runTest(user, {
      ...WARN,
      guardrail_action: null,
      triggered_checks: [],
      guardrail_headers: { "x-agentcc-guardrail-triggered": "true" },
    });

    const card = resultCard();
    expect(await card.findByText("Triggered (200)")).toBeInTheDocument();
    expect(
      card.getByText(
        "A guardrail triggered and let the request through. This gateway does not name the checks.",
      ),
    ).toBeInTheDocument();
  });

  it("shows a log action as logged", async () => {
    const user = userEvent.setup();
    renderTab();
    await runTest(user, {
      ...WARN,
      guardrail_action: "log",
      triggered_checks: ["topic-restriction"],
    });

    const card = resultCard();
    expect(await card.findByText("Logged (200)")).toBeInTheDocument();
    expect(
      card.getByText(
        "A guardrail logged the request and let it through: topic-restriction.",
      ),
    ).toBeInTheDocument();
  });

  it("shows a block as Blocked (403)", async () => {
    const user = userEvent.setup();
    renderTab();
    await runTest(user, {
      status_code: 403,
      body: { error: { type: "guardrail_error", code: "content_blocked" } },
      model: "m",
      blocked: true,
      warned: false,
      triggered_checks: [],
      guardrail_headers: {},
    });

    const card = resultCard();
    expect(await card.findByText("Blocked (403)")).toBeInTheDocument();
    expect(
      card.getByText(/refused the prompt or the model's answer/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/BLOCKED \(446\)/)).toBeNull();
  });

  it.each([429, 500])(
    "shows a provider %i as an error, never green",
    async (code) => {
      const user = userEvent.setup();
      renderTab();
      await runTest(user, {
        status_code: code,
        body: {
          error: { message: "upstream said no", type: "upstream_error" },
        },
        model: "m",
        blocked: false,
        warned: false,
        triggered_checks: [],
        guardrail_headers: {},
      });

      const card = resultCard();
      expect(await card.findByText(`Error (${code})`)).toBeInTheDocument();
      expect(OUTCOMES.failed.tone).toBe("error");
      expect(
        card.getByText(
          `The request failed with HTTP ${code}: upstream said no. No guardrail blocked it.`,
        ),
      ).toBeInTheDocument();
      expect(card.queryByText(/OK \(/)).toBeNull();
      expect(card.queryByText(/passed all guardrails/)).toBeNull();
    },
  );

  it("shows a clean answer as passed", async () => {
    const user = userEvent.setup();
    renderTab();
    await runTest(user, {
      ...WARN,
      warned: false,
      guardrail_action: null,
      triggered_checks: [],
      guardrail_headers: {},
    });

    const card = resultCard();
    expect(await card.findByText("Passed (200)")).toBeInTheDocument();
    expect(
      card.getByText(
        "No guardrail triggered. The request completed (HTTP 200).",
      ),
    ).toBeInTheDocument();
  });

  it("history shows each test's real outcome and status", async () => {
    const user = userEvent.setup();
    renderTab();
    await runTest(user, WARN, "first");
    await screen.findByText("Warned (200)");
    await runTest(
      user,
      {
        status_code: 429,
        body: { error: { message: "slow down" } },
        model: "m",
        blocked: false,
        warned: false,
        triggered_checks: [],
        guardrail_headers: {},
      },
      "second",
    );
    await screen.findByText("Error (429)");

    expect(historyRow("first").getByText("Warned")).toBeInTheDocument();
    expect(historyRow("second").getByText("Error 429")).toBeInTheDocument();
  });
});

describe("Guardrails Logs tab action", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("says what each row's checks did, by the strongest action", async () => {
    get.mockResolvedValue({
      data: {
        result: [
          {
            id: "a",
            request_id: "req-block",
            status_code: 403,
            guardrail_triggered: true,
            guardrail_results: [
              { name: "pii-detection", action: "warn" },
              { name: "secret-detection", action: "block" },
            ],
          },
          {
            id: "b",
            request_id: "req-warn",
            status_code: 200,
            guardrail_triggered: true,
            guardrail_results: [{ name: "pii-detection", action: "warn" }],
          },
          {
            id: "c",
            request_id: "req-log",
            status_code: 200,
            guardrail_triggered: true,
            guardrail_results: [{ name: "topic-restriction", action: "log" }],
          },
        ],
      },
    });
    renderPage(<TriggeredRequestsTab gatewayId="gateway-1" />);

    const blocked = await screen.findByTestId("row-a");
    expect(within(blocked).getByText("Blocked")).toBeInTheDocument();
    expect(within(blocked).getByText("403")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("row-b")).getByText("Warned"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("row-c")).getByText("Logged"),
    ).toBeInTheDocument();
    expect(ACTIONS.blocked.tone).toBe("error");
    expect(ACTIONS.warned.tone).toBe("warning");
    expect(ACTIONS.logged.tone).toBe("neutral");
  });
});
