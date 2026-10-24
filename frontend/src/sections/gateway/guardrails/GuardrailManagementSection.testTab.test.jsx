// The Guardrails Test tab says what happened: a warn is a 2xx with
// x-agentcc-guardrail-triggered: true, a block is a 403 guardrail_error, a
// provider error is an error, and history and the Logs chip show each
// test's outcome and status.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailManagementSection from "./GuardrailManagementSection";

// Gateway config writes are Owner or Admin only (useGatewayAdmin); these
// cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
let mockTab = "playground";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: mockTab }),
    useNavigate: () => vi.fn(),
  };
});
vi.mock("src/utils/axios", () => ({
  default: { post, get },
  endpoints: {
    gateway: {
      testPlayground: (id) => `/agentcc/gateways/${id}/test-playground/`,
      requestLogs: "/agentcc/request-logs/",
    },
  },
  createQueryString: (params) => new URLSearchParams(params).toString(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: {}, isLoading: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useToggleGuardrail: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));
vi.mock("./GuardrailAnalyticsTab", () => ({ default: () => null }));
vi.mock("./FeedbackSummaryCard", () => ({ default: () => null }));
vi.mock("./EditGuardrailDialog", () => ({ default: () => null }));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <GuardrailManagementSection />
    </QueryClientProvider>,
  );
}

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
  const box = screen.getByPlaceholderText("Enter a test prompt...");
  await user.clear(box);
  await user.type(box, prompt);
  await user.click(screen.getByRole("button", { name: /Run Test/ }));
}

function resultCard() {
  return within(
    screen.getByRole("heading", { name: "Result" }).closest(".MuiCard-root"),
  );
}

function historyRow(prompt) {
  return within(screen.getAllByText(prompt).at(-1).closest("tr"));
}

describe("Guardrails Test tab", () => {
  beforeEach(() => {
    mockTab = "playground";
    post.mockReset();
    get.mockReset();
  });

  it("names the codes and headers in its help text", () => {
    renderPage();

    expect(screen.queryByText(/246|446/)).toBeNull();
    const help = screen.getByText(/A block answers HTTP 403/);
    expect(help.textContent).toMatch(/guardrail_error/);
    expect(help.textContent).toMatch(/x-agentcc-guardrail-triggered: true/);
    expect(help.textContent).toMatch(/x-agentcc-guardrails-triggered/);
  });

  it("shows a warn as warned, naming the checks", async () => {
    const user = userEvent.setup();
    renderPage();
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
    renderPage();
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
    renderPage();
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
    renderPage();
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
    expect(screen.queryByText(/BLOCKED \(446\)/)).toBeNull();
  });

  it.each([429, 500])(
    "shows a provider %i as an error, never green",
    async (code) => {
      const user = userEvent.setup();
      renderPage();
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
      const chip = await card.findByText(`Error (${code})`);
      expect(chip.closest(".MuiChip-root").className).toMatch(/colorError/);
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
    renderPage();
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
    renderPage();
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

    const first = historyRow("first");
    const second = historyRow("second");
    expect(first.getByText("Warned")).toBeInTheDocument();
    expect(second.getByText("Error 429")).toBeInTheDocument();
    expect(
      second.getByText("Error 429").closest(".MuiChip-root").className,
    ).toMatch(/colorError/);
  });
});

describe("Guardrails Logs tab chip", () => {
  beforeEach(() => {
    mockTab = "logs";
    get.mockReset();
  });

  it("colours each row by the action its checks took", async () => {
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
    renderPage();

    const blocked = await screen.findByText("Blocked (403)");
    expect(blocked.closest(".MuiChip-root").className).toMatch(/colorError/);
    const warned = screen.getByText("Warned (200)");
    expect(warned.closest(".MuiChip-root").className).toMatch(/colorWarning/);
    const logged = screen.getByText("Logged (200)");
    expect(logged.closest(".MuiChip-root").className).toMatch(/colorInfo/);
  });
});
