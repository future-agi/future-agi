import React from "react";
import { HelmetProvider } from "react-helmet-async";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { cleanup, render, screen, waitFor } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { handleError } from "src/utils/queryErrorHandler";
import { GatewayProvider } from "../context/GatewayContext";
import GatewaySettingsSection from "./GatewaySettingsSection";

// The real Axios client and its interceptor answer every check here, so a
// failed check reaches the page as the interceptor's flat rejection (the
// response body with its statusCode), as it does in the product.
vi.mock("./EmailAlertsCard", () => ({ default: () => null }));
vi.mock("./OrgConfigSection", () => ({ default: () => null }));
// Health Check is Owner or Admin only (useGatewayAdmin): an Admin runs it.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

const server = setupServer();
const CHECK = "*/agentcc/gateways/default/health_check/";
const checkedAt = "2026-04-11T10:05:00Z";
// What the health check answers: its body and its HTTP status.
let answer;
let code;
let queryClient;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  queryClient.clear();
  server.resetHandlers();
});
beforeEach(() => {
  code = 400;
  queryClient = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  server.use(
    http.get("*/agentcc/gateways/", () =>
      HttpResponse.json({
        status: true,
        result: [
          {
            id: "default",
            name: "Test gateway",
            base_url: "http://localhost:8080/v1",
            status: "healthy",
            provider_count: 0,
            model_count: 0,
            last_health_check: checkedAt,
          },
        ],
      }),
    ),
    http.get("*/agentcc/gateways/default/config/", () =>
      HttpResponse.json({
        status: true,
        result: {
          providers: {},
          gateway: { status: "healthy" },
          server: {},
          logging: {},
        },
      }),
    ),
    http.post(CHECK, () => HttpResponse.json(answer, { status: code })),
  );
});

async function runCheck() {
  render(
    <HelmetProvider>
      <QueryClientProvider client={queryClient}>
        <SnackbarProvider>
          <GatewayProvider>
            <GatewaySettingsSection />
          </GatewayProvider>
        </SnackbarProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
  const button = await screen.findByRole("button", { name: "Health Check" });
  await waitFor(() => expect(button).toBeEnabled());
  await userEvent.setup().click(button);
}

describe("Settings health check: why it failed", () => {
  it("names the probe's reason in the toast, and the Status row says when without a filler reason", async () => {
    const reason = "The gateway could not be reached.";
    answer = {
      status: false,
      result: {
        status: "unreachable",
        error: reason,
        last_health_check: checkedAt,
      },
      detail: reason,
    };
    await runCheck();
    expect(
      await screen.findByText(`Health check failed: ${reason}`),
    ).toBeVisible();
    expect(
      screen.getByText(/^Gateway did not answer · checked at /),
    ).toBeVisible();
    expect(screen.queryByText(/health check failed/)).not.toBeInTheDocument();
  });

  it("names the response's detail when the result is a plain sentence", async () => {
    const reason = "This gateway has no address to check.";
    answer = { status: false, result: reason, detail: reason };
    await runCheck();
    expect(
      await screen.findByText(`Health check failed: ${reason}`),
    ).toBeVisible();
  });

  it("never shows a raw upstream diagnostic, in the toast or the Status row", async () => {
    const raw = "Code: 210. DB::NetException: Connection refused";
    answer = {
      status: false,
      result: {
        status: "unreachable",
        error: raw,
        last_health_check: checkedAt,
      },
      detail: raw,
      message: raw,
    };
    await runCheck();
    expect(await screen.findByText("Health check failed")).toBeVisible();
    expect(
      await screen.findByText(/^Gateway did not answer · checked at /),
    ).toBeVisible();
    expect(screen.queryByText(/DB::NetException/)).not.toBeInTheDocument();
  });

  it("a refused check keeps its own toast, and the Status row says when without a filler reason", async () => {
    const refusal = "You do not have permission to perform this action.";
    code = 403;
    answer = {
      status: false,
      code: "permission_denied",
      detail: refusal,
      result: refusal,
    };
    await runCheck();
    expect(
      await screen.findByText("Health check not authorized"),
    ).toBeVisible();
    expect(
      screen.getByText(/^Health check not authorized · attempted at /),
    ).toBeVisible();
  });
});
