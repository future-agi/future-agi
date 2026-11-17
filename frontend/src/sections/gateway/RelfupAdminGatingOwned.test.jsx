import React from "react";
import { HelmetProvider } from "react-helmet-async";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import OrgConfigEditor from "./settings/OrgConfigEditor";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "./testing/adminOnly";

// The gateway admin role check: the Org Config editor's Save & Activate is
// disabled with "Owner or Admin only" for a Member, a Viewer or a user whose
// level is not loaded. Reading stays open. The Guardrails page's cases (rule
// switch, open, Save and Remove, Add check, Run test) are in
// guardrails/rules/__tests__/RulesView.test.jsx and
// guardrails/test/__tests__/GuardrailTestTab.test.jsx, on the Guardrails
// page.

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
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
    </HelmetProvider>,
  );

beforeEach(() => {
  mockOrgLevel = 8;
  mockTab = undefined;
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
