import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import ModelTryIt from "./ModelTryIt";
import { ACCESS } from "../catalogRows";
import { interpretTestResult } from "../../guardrails/test/interpret-result";
import TestResult from "../../guardrails/test/TestResult";

import { CONTRACT_CASES } from "../../utils/__tests__/managedRefusalContractCases";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post },
}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: () => ({ data: false }),
}));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("notistack", async (original) => ({ ...(await original()), enqueueSnackbar: vi.fn() }));

const row = {
  id: "openai/gpt-4o-mini",
  type: "chat",
  access: ACCESS.MANAGED,
  managedOn: true,
  ownRoutes: [],
  model: {},
};
// Synthetic monetary/run values must never enter a recognized alert body.
const RUNTIME_DETAIL =
  "run:fixture-run has $12.345678 left; required $98.765432";
const UNPRICED = "model pricing is not available for Future AGI credits";
const BILLING = "/dashboard/settings/billing#ai-gateway-credits";
const BUDGETS = "/dashboard/gateway/budgets";
const CASES = [
  [
    402,
    "managed_credits_exhausted",
    "Future AGI credits are used up",
    "Top up credits",
    BILLING,
  ],
  [
    402,
    "managed_credits_insufficient",
    "Not enough Future AGI credits",
    "Top up credits",
    BILLING,
  ],
  [
    402,
    "agent_budget_exhausted",
    "Agent budget exhausted",
    "Review the agent run budget",
    null,
  ],
  [
    429,
    "budget_exceeded",
    "Gateway budget exceeded",
    "Open gateway budgets",
    BUDGETS,
  ],
  [
    429,
    "org_budget_exceeded",
    "Organization budget exceeded",
    "Open gateway budgets",
    BUDGETS,
  ],
  [
    429,
    "rate_limit_exceeded",
    "Rate limit reached",
    "Try again after waiting",
    null,
  ],
  [
    503,
    "managed_billing_unavailable",
    "Billing is temporarily unavailable",
    "Try again later",
    null,
  ],
  [
    403,
    "future_agi_credits_off",
    "Future AGI credits are off",
    "Ask an Owner or Admin to contact your Future AGI account team",
    null,
  ],
  [
    403,
    "permission_denied",
    "Model pricing is unavailable",
    "Choose a priced model or use your own provider key",
    null,
  ],
  [
    402,
    "managed_credits_insufficient",
    "Your balance is in use",
    "Try again after those requests finish",
    null,
    { limit_source: "agentcc_in_flight", remedy_hint: "retry" },
  ],
].map(([status, code, title, action, href, metadata = {}]) => ({
  name: `${code} / ${title}`,
  status,
  code,
  title,
  action,
  href,
  metadata,
  showsRetryAfter: code === "rate_limit_exceeded",
}));

// errors.go:25-69: the transient producer omits reason and carries PaymentMeta.
const SPEND_CASES = [
  [
    "request_exceeds_minute_cap",
    "Request exceeds the spending limit",
    "Lower max_tokens or reduce the request",
  ],
  [
    "request_exceeds_hour_cap",
    "Request exceeds the spending limit",
    "Lower max_tokens or reduce the request",
  ],
  [
    "new_org_daily_cap",
    "New organization spending limit reached",
    "Contact support",
  ],
].map(([reason, title, action]) => ({
  name: reason,
  status: 429,
  code: "managed_spend_velocity",
  title,
  action,
  metadata: { reason },
  showsRetryAfter: false,
}));
for (const scope of ["org", "key:fixture-key"]) {
  SPEND_CASES.push({
    name: `transient ${scope}`,
    status: 429,
    code: "managed_spend_velocity",
    title: "Requests are temporarily limited",
    action: "Try again after waiting",
    metadata: {
      limit_source: "agentcc_spend_guard",
      remedy_hint: "retry",
      scope,
      resets_at: "2026-11-06T10:00:00Z",
    },
    showsRetryAfter: true,
  });
}
SPEND_CASES.push({
  name: "unknown spend reason",
  status: 429,
  code: "managed_spend_velocity",
  title: "Managed spending is limited",
  action: "Review the spending limit with an administrator",
  metadata: { reason: "future_reason" },
  showsRetryAfter: false,
});
const ALL_CASES = [
  ...CASES,
  ...SPEND_CASES,
  ...CONTRACT_CASES.map((testCase) => ({
    ...testCase,
    name: `r3 ${testCase.code}`,
    message: RUNTIME_DETAIL,
    metadata: {},
    href:
      testCase.action === "Top up credits"
        ? BILLING
        : testCase.action === "Open gateway budgets"
          ? BUDGETS
          : testCase.code === "invalid_api_key"
            ? "/dashboard/gateway/keys"
            : null,
    showsRetryAfter: [
      "rate_limit_exceeded",
      "budget_throttled",
      "token_rate_limit_exceeded",
      "provider_busy",
      "idempotency_in_progress",
    ].includes(testCase.code),
  })),
];
const refusal = ({ status, code, metadata, message }) => ({
  status_code: status,
  body: {
    error: {
      code,
      type: "rate_limit_error",
      message:
        message ??
        (code === "permission_denied"
          ? `${UNPRICED}: ${RUNTIME_DETAIL}`
          : RUNTIME_DETAIL),
      metadata,
    },
  },
  // Even an unexpected header must not turn a non-waitable reason into retry.
  guardrail_headers: { "Retry-After": "9" },
  blocked: false,
});

beforeEach(() => {
  post.mockReset();
});
afterEach(cleanup);

function expectRemedy({
  title,
  action,
  href,
  showsRetryAfter,
  preservesMessage,
  metadata,
  code,
}) {
  const alert = screen.getByRole("alert");
  expect(within(alert).getByText(title)).toBeInTheDocument();
  if (href) {
    expect(within(alert).getByRole("link", { name: action })).toHaveAttribute(
      "href",
      href,
    );
  } else {
    expect(within(alert).getByText(action)).toBeInTheDocument();
    expect(within(alert).queryByRole("link", { name: action })).toBeNull();
    expect(within(alert).queryByRole("button", { name: action })).toBeNull();
  }
  if (showsRetryAfter) expect(alert).toHaveTextContent("Retry-After: 9");
  else expect(alert).not.toHaveTextContent("Retry-After");
  if (preservesMessage) expect(alert).toHaveTextContent(RUNTIME_DETAIL);
  else
    expect(alert).not.toHaveTextContent(
      /\$|12\.345678|98\.765432|fixture-run|fixture-key|run:|2026-11-06/,
    );
  if (metadata.reason && !preservesMessage)
    expect(alert).not.toHaveTextContent(/wait/i);
  if (showsRetryAfter) {
    expect(alert).toHaveTextContent("Wait before trying again.");
    expect(alert).toHaveTextContent("not retry automatically");
  }
  if (code === "idempotency_in_progress") {
    expect(alert).toHaveTextContent("request body unchanged");
    expect(alert).toHaveTextContent("same idempotency key");
  }
}

describe.each(["json", "stream", "http rejection", "raw http rejection"])(
  "Try it managed refusals: %s",
  (transport) => {
    it.each(ALL_CASES)(
      "shows the mapped title, body and remedy for $name",
      async (testCase) => {
        const result = refusal(testCase);
        if (transport === "http rejection") {
          post.mockRejectedValue({
            statusCode: testCase.status,
            ...result.body,
            responseHeaders: result.guardrail_headers,
          });
        } else if (transport === "raw http rejection") {
          post.mockRejectedValue({
            response: {
              status: testCase.status,
              data: result.body,
              headers: result.guardrail_headers,
            },
          });
        } else if (transport === "stream") {
          const data = `event: result\ndata: ${JSON.stringify(result)}\n\n`;
          post.mockImplementation(async (_url, _body, options) => {
            options.onDownloadProgress?.({
              event: { target: { responseText: data } },
            });
            return { data };
          });
        } else {
          post.mockResolvedValue({ data: { result } });
        }
        render(
          <MemoryRouter>
            <ModelTryIt row={row} gatewayId="gw-1" />
          </MemoryRouter>,
        );
        if (transport === "json") {
          await userEvent.click(
            screen.getByRole("checkbox", { name: "Stream the answer" }),
          );
        }
        await userEvent.click(screen.getByRole("button", { name: "Run" }));
        await screen.findByText(testCase.title);
        await waitFor(() =>
          expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
        );
        expectRemedy(testCase);
        expect(post).toHaveBeenCalledTimes(1);
      },
    );
  },
);

describe("Guardrails uses the same refusal presentation", () => {
  it.each(ALL_CASES)(
    "shows the same title, body and remedy for $name",
    (testCase) => {
      render(
        <MemoryRouter>
          <TestResult result={interpretTestResult(refusal(testCase))} />
        </MemoryRouter>,
      );
      expectRemedy(testCase);
      expect(post).not.toHaveBeenCalled();
    },
  );
});

describe("Try it generic diagnostic evidence", () => {
  it.each([
    [429, { message: "Gateway request failed.", code: "upstream_4xx" }],
    [400, { message: "Lower max_tokens.", code: "invalid_request", type: "invalid_request_error", param: "max_tokens" }],
    [500, { message: "Gateway request failed.", code: "upstream_5xx" }],
  ])("keeps useful HTTP %s copy and copies only supplied evidence", async (status, error) => {
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    post.mockResolvedValue({ data: { result: { status_code: status, body: { error }, guardrail_headers: {} } } });
    render(<MemoryRouter><ModelTryIt row={row} gatewayId="gw-1" /></MemoryRouter>);
    await user.click(screen.getByRole("checkbox", { name: "Stream the answer" }));
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText(error.message)).toBeInTheDocument();
    const details = screen.getByText("Request diagnostics").closest("details");
    expect(details).not.toHaveAttribute("open");
    await user.click(screen.getByText("Request diagnostics"));
    expect(details).toHaveAttribute("open");
    const evidence = JSON.parse((await within(details).findByLabelText("Received request evidence")).textContent);
    expect(evidence).toEqual({ outcome: status < 500 ? "refused" : "unknown", status, ...error });
    expect(details).not.toHaveTextContent(/requestId|retryAfter|Authorization/);
    await user.click(within(details).getByRole("button", { name: "Copy diagnostics" }));
    expect(JSON.parse(clipboard.mock.calls[0][0])).toEqual(evidence);
    expect(post).toHaveBeenCalledTimes(1);
    clipboard.mockRestore();
  });

  it("shows optional response evidence without including arbitrary payload or request config", async () => {
    post.mockRejectedValue({
      response: {
        status: 400,
        data: { error: { message: "Unsupported temperature.", code: "invalid_request", param: "temperature" }, private_payload: "fixture-private" },
        headers: { "x-request-id": "console-request-04", "Retry-After": "7" },
      },
      config: { headers: { Authorization: "Bearer fixture-credential" } },
    });
    render(<MemoryRouter><ModelTryIt row={row} gatewayId="gw-1" /></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("Unsupported temperature.");
    await userEvent.click(screen.getByText("Request diagnostics"));
    expect(JSON.parse((await screen.findByLabelText("Received request evidence")).textContent)).toEqual({
      outcome: "refused", status: 400, message: "Unsupported temperature.", code: "invalid_request", param: "temperature", requestId: "console-request-04", retryAfter: "7",
    });
    expect(document.body).not.toHaveTextContent(/fixture-private|fixture-credential|Authorization/);
    expect(post).toHaveBeenCalledTimes(1);
  });
});

// A typed 404 is a refusal, not proof that the streaming endpoint is missing.
describe("unknown 404 streaming refusals", () => {
  it.each(["shared", "raw", "wrapped", "top-level code"])(
    "does not replay %s errors",
    async (shape) => {
      const error = {
        error: { code: "unknown_future_code", message: "Request refused" },
      };
      const failure =
        shape === "shared"
          ? { statusCode: 404, ...error }
          : shape === "raw"
            ? { response: { status: 404, data: error } }
            : shape === "wrapped"
              ? {
                  statusCode: 404,
                  result: { status_code: 404, body: JSON.stringify(error) },
                }
              : {
                  statusCode: 404,
                  detail: "Not found.",
                  code: "unknown_future_code",
                };
      post.mockRejectedValue(failure);
      render(
        <MemoryRouter>
          <ModelTryIt row={row} gatewayId="gw-1" />
        </MemoryRouter>,
      );
      await userEvent.click(screen.getByRole("button", { name: "Run" }));
      await screen.findByText("Request failed");
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Run" })).toBeEnabled(),
      );
      expect(post).toHaveBeenCalledTimes(1);
    },
  );
});
