import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { chip } from "src/theme/overrides/components/chip";
import GuardrailManagementSection from "./GuardrailManagementSection";

let mockTab;
let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
};

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
    mockTab = undefined;
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
    };
  });

  describe.each(["light", "dark"])("log status badges (%s theme)", (mode) => {
    it.each([
      [403, "error"],
      [400, "error"],
      [446, "error"],
      [499, "error"],
      [500, "error"],
      [599, "error"],
      [246, "warning"],
      [200, "success"],
      [204, "success"],
      [299, "success"],
      [199, "default"],
      [300, "default"],
      [399, "default"],
      [600, "default"],
      [null, "default"],
      [undefined, "default"],
    ])("renders status %s with the %s background", (statusCode, color) => {
      mockTab = "logs";
      const theme = createTheme({ palette: palette(mode) });
      theme.components = chip(theme);
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      queryClient.setQueryData(
        ["agentcc-guardrail-logs", "gateway-1"],
        [
          {
            id: "test-request",
            request_id: "test-request",
            started_at: "2026-04-13T10:00:00Z",
            status_code: statusCode,
            model: "test-model",
          },
        ],
      );

      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <GuardrailManagementSection />
        </QueryClientProvider>,
        { theme },
      );

      try {
        const badge = screen
          .getByRole("cell", { name: String(statusCode ?? "\u2014") })
          .querySelector(".MuiChip-root");
        expect(badge).toHaveStyle({
          "background-color":
            color === "default"
              ? theme.palette.text.primary
              : theme.palette[color].main,
        });
      } finally {
        unmount();
        queryClient.clear();
      }
    });
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
