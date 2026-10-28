import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import GuardrailManagementSection from "./GuardrailManagementSection";

let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
};
const mockUpdateGuardrail = vi.fn();

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
  useUpdateGuardrail: () => ({
    mutate: mockUpdateGuardrail,
    isPending: false,
  }),
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

vi.mock("../settings/GuardrailConfigTab", () => ({
  default: () => <div>Guardrail config tab</div>,
}));

const providerConfig = {
  stage: "post",
  provider: "custom-provider",
  words: ["unsafe"],
  mode: "provider-mode",
  api_key: "__encrypted__",
  custom_option: { keep: true },
};
const savedReflection = {
  name: "keyword-blocklist",
  enabled: true,
  action: "block",
  stage: "post",
  mode: "sync",
  threshold: 0.8,
  config: {
    ...providerConfig,
    on_block: "reflect",
    reflect_max_attempts: 2,
    reflect_debug: true,
  },
};

const openOverviewEditor = (rule = savedReflection) => {
  mockOrgConfigReturn = {
    data: { guardrails: { rules: [rule] } },
    isLoading: false,
  };
  render(<GuardrailManagementSection />);
  fireEvent.click(screen.getByTitle("Edit"));
};

const choose = (label, option) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: option, exact: true }));
};

const expectSavedRule = (rule) => {
  expect(mockUpdateGuardrail).toHaveBeenCalledTimes(1);
  expect(mockUpdateGuardrail).toHaveBeenCalledWith(
    { gatewayId: "gateway-1", name: rule.name, config: rule },
    expect.any(Object),
  );
};

describe("GuardrailManagementSection", () => {
  beforeEach(() => {
    mockUpdateGuardrail.mockClear();
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
    };
  });

  it.each(["warn", "log"])(
    "removes retries when Overview changes a reflected rule to %s",
    (action) => {
      openOverviewEditor();
      choose("Action", action);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      expectSavedRule({
        ...savedReflection,
        action,
        config: { ...providerConfig, on_block: "reject" },
      });
      expect(savedReflection.config).toEqual({
        ...providerConfig,
        on_block: "reflect",
        reflect_max_attempts: 2,
        reflect_debug: true,
      });
    },
  );

  it("preserves retries when an Overview edit stays block/post/sync", () => {
    openOverviewEditor();
    fireEvent.change(screen.getByRole("spinbutton", { name: "Threshold" }), {
      target: { value: "0.5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expectSavedRule({ ...savedReflection, threshold: 0.5 });
  });

  it("removes retries and updates config.stage when Overview selects pre", () => {
    openOverviewEditor();
    choose("Stage", "pre");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expectSavedRule({
      ...savedReflection,
      stage: "pre",
      config: { ...providerConfig, stage: "pre", on_block: "reject" },
    });
  });

  it("removes incompatible retries on an asynchronous rule", () => {
    openOverviewEditor({ ...savedReflection, mode: "async" });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expectSavedRule({
      ...savedReflection,
      mode: "async",
      config: { ...providerConfig, on_block: "reject" },
    });
  });

  it("uses the nested response stage rather than a stale top-level pre", () => {
    openOverviewEditor({ ...savedReflection, stage: "pre" });
    expect(screen.getByRole("combobox", { name: "Stage" })).toHaveTextContent(
      "post",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expectSavedRule(savedReflection);
  });

  it.each(["stage", "phase"])(
    "keeps the response stage inferred from %s when removing retries",
    (field) => {
      const inferredConfig = { ...savedReflection.config };
      delete inferredConfig.stage;
      const rule = {
        ...savedReflection,
        stage: undefined,
        [field]: "post",
        config: inferredConfig,
      };
      openOverviewEditor(rule);
      choose("Action", "warn");
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      expectSavedRule({
        ...rule,
        stage: "post",
        action: "warn",
        config: { ...providerConfig, on_block: "reject" },
      });
    },
  );

  it.each([
    ["hallucination-detection", "pre"],
    ["prompt-injection", "post"],
  ])("does not offer an unsupported stage for %s", (name, stage) => {
    openOverviewEditor({ name, action: "block", mode: "sync", config: {} });
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Stage" }));

    expect(screen.getByRole("option", { name: stage })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.queryByRole("option", { name: "both" })).toBeNull();
  });

  it("does not inject retry defaults when saving a legacy rule", () => {
    const rule = {
      name: "keyword-blocklist",
      stage: "pre",
      action: "warn",
      mode: "sync",
      config: { words: ["unsafe"], mode: "provider-mode" },
    };
    openOverviewEditor(rule);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expectSavedRule(rule);
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
