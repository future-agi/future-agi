import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import GuardrailManagementSection from "./GuardrailManagementSection";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The role check: switching, editing and saving a
// guardrail, and the Test tab (it pushes the config), are Owner or Admin
// only. The cases below run as an Admin unless one says otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));
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
  // eslint-disable-next-line react/prop-types
  default: ({ onChange }) => (
    <div>
      Guardrail config tab
      <button type="button" onClick={() => onChange({ rules: [] })}>
        Change a rule
      </button>
    </div>
  ),
}));

const ONE_RULE = {
  data: {
    guardrails: {
      rules: [
        { name: "pii-detector", stage: "pre", action: "block", enabled: true },
      ],
    },
  },
  isLoading: false,
};

describe("GuardrailManagementSection", () => {
  beforeEach(() => {
    mockOrgLevel = 8;
    mockTab = undefined;
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
    };
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads every tab with each guardrail write disabled",
    async (_who, level) => {
      mockOrgLevel = level;
      mockOrgConfigReturn = ONE_RULE;
      const user = userEvent.setup();

      const overview = render(<GuardrailManagementSection />);
      expect(screen.getByText("pii-detector")).toBeInTheDocument();
      expectAdminOnly(
        screen.getByRole("checkbox", { name: /turn pii-detector on or off/i }),
      );
      expectAdminOnly(
        screen.getByRole("button", { name: "Edit pii-detector" }),
      );
      overview.unmount();

      mockTab = "configuration";
      const rules = render(<GuardrailManagementSection />);
      await user.click(screen.getByRole("button", { name: "Change a rule" }));
      expectAdminOnly(screen.getByRole("button", { name: "Save & Activate" }));
      rules.unmount();

      mockTab = "playground";
      render(<GuardrailManagementSection />);
      await user.type(
        screen.getByPlaceholderText("Enter a test prompt..."),
        "hello",
      );
      expectAdminOnly(screen.getByRole("button", { name: "Run Test" }));
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can switch, edit, save and test",
    async (_who, level) => {
      mockOrgLevel = level;
      mockOrgConfigReturn = ONE_RULE;
      const user = userEvent.setup();

      const overview = render(<GuardrailManagementSection />);
      expectEditable(
        screen.getByRole("checkbox", { name: /turn pii-detector on or off/i }),
      );
      expectEditable(screen.getByRole("button", { name: "Edit pii-detector" }));
      overview.unmount();

      mockTab = "configuration";
      const rules = render(<GuardrailManagementSection />);
      await user.click(screen.getByRole("button", { name: "Change a rule" }));
      expectEditable(screen.getByRole("button", { name: "Save & Activate" }));
      rules.unmount();

      mockTab = "playground";
      render(<GuardrailManagementSection />);
      await user.type(
        screen.getByPlaceholderText("Enter a test prompt..."),
        "hello",
      );
      expectEditable(screen.getByRole("button", { name: "Run Test" }));
    },
  );

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
