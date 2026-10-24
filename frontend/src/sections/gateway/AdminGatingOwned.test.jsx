import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "src/utils/test-utils";
import GuardrailManagementSection from "./guardrails/GuardrailManagementSection";
import OrgConfigEditor from "./settings/OrgConfigEditor";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "./testing/adminOnly";

// The gateway admin role check on the Guardrails page and the Org Config
// editor: the Guardrails page's switch, edit, Save & Activate and Run Test,
// and the Org Config editor's Save & Activate are disabled with "Owner or
// Admin only" for a Member, a Viewer or a user whose level is not loaded.
// Reading stays open.

let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

let mockTab;
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ tab: mockTab }),
  useNavigate: () => vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  endpoints: {
    gateway: {
      orgConfig: { create: "/agentcc/org-configs/" },
      updateConfig: (id) => `/agentcc/gateways/${id}/update-config/`,
      testPlayground: (id) => `/agentcc/gateways/${id}/test-playground/`,
    },
  },
  createQueryString: vi.fn(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const RULES = {
  guardrails: {
    rules: [{ name: "pii-detector", stage: "pre", action: "block" }],
  },
};
vi.mock("./providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: RULES, isLoading: false }),
}));
vi.mock("./providers/hooks/useGatewayConfig", async (importOriginal) => ({
  ...(await importOriginal()),
  useToggleGuardrail: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1", isLoading: false }),
}));
vi.mock("./guardrails/GuardrailAnalyticsTab", () => ({ default: () => null }));
vi.mock("./guardrails/FeedbackSummaryCard", () => ({ default: () => null }));
vi.mock("./guardrails/EditGuardrailDialog", () => ({ default: () => null }));

// The rules editor stand-in: one button makes the draft dirty.
const MockRulesTab = ({ onChange }) => (
  <button
    type="button"
    onClick={() =>
      onChange({ checks: { "pii-detection": { enabled: false } } })
    }
  >
    Change a rule
  </button>
);
MockRulesTab.propTypes = { onChange: PropTypes.func.isRequired };
vi.mock("./settings/GuardrailConfigTab", () => ({
  default: (props) => <MockRulesTab {...props} />,
}));
vi.mock("./settings/ProviderConfigTab", () => ({ default: () => null }));

const renderWithClient = (ui) =>
  render(
    <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>,
  );

beforeEach(() => {
  mockOrgLevel = 8;
  mockTab = undefined;
});

describe("Guardrails page", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the checks with the switch and Edit disabled",
    (_who, level) => {
      mockOrgLevel = level;
      renderWithClient(<GuardrailManagementSection />);
      expect(screen.getByText("pii-detector")).toBeInTheDocument();
      expectAdminOnly(
        screen.getByRole("checkbox", { name: "Turn pii-detector on or off" }),
      );
      expectAdminOnly(
        screen.getByRole("button", { name: "Edit pii-detector" }),
      );
    },
  );

  it.each(ADMIN_LEVELS)("%s can switch and edit a check", (_who, level) => {
    mockOrgLevel = level;
    renderWithClient(<GuardrailManagementSection />);
    expectEditable(
      screen.getByRole("checkbox", { name: "Turn pii-detector on or off" }),
    );
    expectEditable(screen.getByRole("button", { name: "Edit pii-detector" }));
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s can draft a rule change but not save it",
    (_who, level) => {
      mockOrgLevel = level;
      mockTab = "configuration";
      renderWithClient(<GuardrailManagementSection />);
      fireEvent.click(screen.getByRole("button", { name: "Change a rule" }));
      expectAdminOnly(screen.getByRole("button", { name: "Save & Activate" }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can save a rule change", (_who, level) => {
    mockOrgLevel = level;
    mockTab = "configuration";
    renderWithClient(<GuardrailManagementSection />);
    fireEvent.click(screen.getByRole("button", { name: "Change a rule" }));
    expectEditable(screen.getByRole("button", { name: "Save & Activate" }));
  });

  it.each(NOT_ADMIN_LEVELS)("%s cannot run a test", (_who, level) => {
    mockOrgLevel = level;
    mockTab = "playground";
    renderWithClient(<GuardrailManagementSection />);
    fireEvent.change(screen.getAllByRole("textbox")[0], {
      target: { value: "What is the capital of France?" },
    });
    expectAdminOnly(screen.getByRole("button", { name: "Run Test" }));
  });

  it.each(ADMIN_LEVELS)("%s can run a test", (_who, level) => {
    mockOrgLevel = level;
    mockTab = "playground";
    renderWithClient(<GuardrailManagementSection />);
    fireEvent.change(screen.getAllByRole("textbox")[0], {
      target: { value: "What is the capital of France?" },
    });
    expectEditable(screen.getByRole("button", { name: "Run Test" }));
  });
});

describe("Org Config editor", () => {
  it.each(NOT_ADMIN_LEVELS)(
    "%s opens the editor to read it, with Save & Activate disabled",
    (_who, level) => {
      mockOrgLevel = level;
      renderWithClient(
        <OrgConfigEditor open onClose={vi.fn()} initialConfig={{}} />,
      );
      expectAdminOnly(screen.getByRole("button", { name: "Save & Activate" }));
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
    },
  );

  it.each(ADMIN_LEVELS)("%s can save the config", (_who, level) => {
    mockOrgLevel = level;
    renderWithClient(
      <OrgConfigEditor open onClose={vi.fn()} initialConfig={{}} />,
    );
    expectEditable(screen.getByRole("button", { name: "Save & Activate" }));
  });
});
