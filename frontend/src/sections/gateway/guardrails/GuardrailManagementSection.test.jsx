import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import GuardrailManagementSection from "./GuardrailManagementSection";

let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
};

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ tab: undefined }),
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

  it("shows the stage each guardrail runs at", () => {
    mockOrgConfigReturn = {
      data: {
        guardrails: {
          rules: [
            { name: "lakera-guard", stage: "both", enabled: true },
            {
              name: "futureagi-eval",
              stage: "post",
              phase: "pre",
              enabled: true,
            },
            { name: "hallucination-detection", stage: "pre", enabled: true },
            { name: "pii-detector", stage: "post", enabled: true },
          ],
        },
      },
      isLoading: false,
    };

    render(<GuardrailManagementSection />);

    const stageOf = (name) =>
      within(screen.getByText(name).closest("tr")).getByText(/LLM$/)
        .textContent;
    expect(stageOf("lakera-guard")).toBe("Before and after LLM");
    expect(stageOf("futureagi-eval")).toBe("After LLM");
    expect(stageOf("hallucination-detection")).toBe("After LLM");
    expect(stageOf("pii-detector")).toBe("Before LLM");
  });
});
