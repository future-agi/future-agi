import React from "react";
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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import { GatewayProvider } from "../context/GatewayContext";
import GatewaySettingsSection from "./GatewaySettingsSection";

// Keep the real button, mutation, Axios interceptors, gateway query/normalizer,
// and snackbar. Only unrelated Settings panels are omitted.
vi.mock("./EmailAlertsCard", () => ({ default: () => null }));
vi.mock("./OrgConfigSection", () => ({ default: () => null }));

const server = setupServer();
const beforeCheck = "2026-04-11T10:00:00Z";
const afterCheck = "2026-04-11T10:05:00Z";
let lastHealthCheck;
let gatewayStatus;
let queryClient;
let completeCheck;
let healthRequests;

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  cleanup();
  queryClient.clear();
  server.resetHandlers();
  vi.unstubAllEnvs();
});
beforeEach(() => {
  vi.stubEnv("VITE_API_CONTRACT_STRICT_RESPONSES", "true");
  healthRequests = 0;
  lastHealthCheck = beforeCheck;
  gatewayStatus = "healthy";
  queryClient = new QueryClient({
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
            status: gatewayStatus,
            provider_count: 0,
            model_count: 0,
            last_health_check: lastHealthCheck,
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
    http.post(
      "*/agentcc/gateways/default/health_check/",
      async ({ request }) => {
        healthRequests += 1;
        expect(await request.json()).toEqual({});
        return new Promise((resolve) => {
          completeCheck = resolve;
        });
      },
    ),
  );
});

function renderSettings() {
  return render(
    <QueryClientProvider client={queryClient}>
      <SnackbarProvider>
        <GatewayProvider>
          <GatewaySettingsSection />
        </GatewayProvider>
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

async function startCheck() {
  renderSettings();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Health Check" }));
  await waitFor(() => expect(healthRequests).toBe(1));
  expect(screen.getByRole("button", { name: "Checking..." })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Checking..." }));
  expect(healthRequests).toBe(1);
}

describe("Settings health check", () => {
  it("sends a POST, shows pending feedback, then displays the refreshed server timestamp", async () => {
    await startCheck();
    lastHealthCheck = afterCheck;
    completeCheck(
      HttpResponse.json({
        status: true,
        result: {
          status: "healthy",
          last_health_check: afterCheck,
          health: { status: "ok" },
          providers: { providers: [] },
          provider_count: 0,
          model_count: 0,
        },
      }),
    );
    expect(await screen.findByText("Health check complete")).toBeVisible();
    expect(
      await screen.findByText(new Date(afterCheck).toLocaleString()),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Health Check" }),
      ).toBeEnabled(),
    );
  });

  it("shows the server error and refreshes the timestamp after an unreachable check", async () => {
    await startCheck();
    lastHealthCheck = afterCheck;
    gatewayStatus = "unreachable";
    completeCheck(
      HttpResponse.json(
        {
          status: false,
          result: {
            status: "unreachable",
            error: "Connection refused",
            last_health_check: afterCheck,
          },
          detail: "Connection refused",
          details: { status: ["unreachable"], error: ["Connection refused"] },
        },
        { status: 400 },
      ),
    );
    expect(
      await screen.findByText("Health check failed: Connection refused"),
    ).toBeVisible();
    expect(
      await screen.findByText(new Date(afterCheck).toLocaleString()),
    ).toBeVisible();
    expect(screen.getByText("unreachable")).toBeVisible();
    expect(screen.queryByText("Health check complete")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Health Check" })).toBeEnabled();
  });
});
