import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axiosInstance from "src/utils/axios";
import { validateContractedRequestConfig } from "src/api/contracts/openapi-contract";
import GuardrailManagementSection from "./GuardrailManagementSection";

let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
};

let mockParams = { tab: undefined };

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => mockParams,
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
    mockParams = { tab: undefined };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists guardrail-triggered requests with a query the API contract accepts", async () => {
    mockParams = { tab: "logs" };
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

  it.each([403, 446])(
    "shows the real status code on a blocked test result (%i)",
    async (statusCode) => {
      mockParams = { tab: "playground" };
      vi.spyOn(axiosInstance, "post").mockResolvedValue({
        data: {
          status: true,
          result: {
            status_code: statusCode,
            body: { error: { message: "Request blocked by guardrail" } },
            guardrail_headers: {},
            model: "gpt-4o-mini",
            blocked: true,
            warned: false,
          },
        },
      });
      const user = userEvent.setup();

      render(<GuardrailManagementSection />);

      await user.click(screen.getByText("Safe prompt"));
      await user.click(screen.getByRole("button", { name: "Run Test" }));

      expect(
        await screen.findByText(`BLOCKED (${statusCode})`),
      ).toBeInTheDocument();
    },
  );

  const runPlayground = async (result) => {
    mockParams = { tab: "playground" };
    vi.spyOn(axiosInstance, "post").mockResolvedValue({
      data: {
        status: true,
        result: {
          body: {},
          guardrail_headers: {},
          model: "gpt-4o-mini",
          ...result,
        },
      },
    });
    const user = userEvent.setup();
    render(<GuardrailManagementSection />);
    await user.click(screen.getByText("Safe prompt"));
    await user.click(screen.getByRole("button", { name: "Run Test" }));
  };

  it("shows the real status code on a warned test result", async () => {
    await runPlayground({ status_code: 200, blocked: false, warned: true });

    expect(await screen.findByText("WARNING (200)")).toBeInTheDocument();
  });

  it("shows the real status code in the Recent Tests history", async () => {
    await runPlayground({ status_code: 201, blocked: false, warned: false });

    expect(await screen.findByText("OK (201)")).toBeInTheDocument();
    expect(screen.getByText("201")).toBeInTheDocument();
  });

  it("marks a blocked (403) row in the Logs tab as an error", async () => {
    mockParams = { tab: "logs" };
    vi.spyOn(axiosInstance, "get").mockResolvedValue({
      data: {
        results: [
          {
            id: "log-1",
            request_id: "req-blocked-1",
            started_at: "2026-10-28T19:46:17Z",
            model: "gpt-4o-mini",
            status_code: 403,
            guardrail_triggered: true,
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

    const chip = (await screen.findByText("403")).closest(".MuiChip-root");
    expect(chip).toHaveClass("MuiChip-colorError");
  });
});
