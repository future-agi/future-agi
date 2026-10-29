import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import axiosInstance from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import GuardrailManagementSection from "./GuardrailManagementSection";

let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
};

let mockTab;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: mockTab }),
    useNavigate: () => vi.fn(),
  };
});

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => mockOrgConfigReturn,
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useToggleGuardrail: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));

vi.mock("./GuardrailAnalyticsTab", () => ({
  default: () => <div>Analytics tab</div>,
}));

vi.mock("./FeedbackSummaryCard", () => ({
  default: () => <div>Feedback summary</div>,
}));

vi.mock("./EditGuardrailDialog", () => ({
  default: () => null,
}));

vi.mock("../settings/GuardrailConfigTab", () => ({
  default: () => <div>Guardrail config tab</div>,
}));

describe("GuardrailManagementSection", () => {
  beforeEach(() => {
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
    };
    mockTab = undefined;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists guardrail-triggered requests with a query the API contract accepts", async () => {
    mockTab = "logs";
    const get = vi.spyOn(axiosInstance, "get").mockResolvedValue({
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
          },
        ],
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <GuardrailManagementSection />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("gpt-4o-mini")).toBeInTheDocument();
    const [url] = get.mock.calls[0];
    expect(url).toContain("guardrail_triggered=true");
    // Outside production builds the axios contract guard rejects undeclared
    // query params before the request is sent, so the tab stayed empty.
    expect(
      validateContractedRequestConfig({ url, method: "get" }),
    ).toMatchObject({ ok: true });
  });

  it("derives the overview type label from the guardrail name", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "pii-detector",
              stage: "pre",
              action: "block",
              enabled: true,
            },
            {
              name: "futureagi-eval",
              stage: "pre",
              action: "block",
              enabled: true,
            },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    expect(screen.getByText("PII")).toBeInTheDocument();
    expect(screen.getByText("Model-based")).toBeInTheDocument();
  });

  it("shows guardrail action in the overview instead of execution mode", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            {
              name: "keyword-blocklist",
              stage: "pre",
              mode: "sync",
              action: "block",
              enabled: true,
              config: { words: ["hello", "world"] },
            },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    expect(screen.getByText("Action")).toBeInTheDocument();
    expect(screen.getByText("block")).toBeInTheDocument();
    expect(screen.queryByText("sync")).not.toBeInTheDocument();
  });
});
