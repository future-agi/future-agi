import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import OrgConfigSection from "./OrgConfigSection";
import RoutingConfigTab from "./RoutingConfigTab";

const state = vi.hoisted(() => ({
  canEdit: false,
  config: { guardrail_pipeline: { enabled: false } },
  isLoading: false,
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: state.canEdit }),
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "synthetic-gateway" }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({ data: state.config, isLoading: state.isLoading }),
  useProviderHealth: () => ({ data: { providers: [] } }),
}));
vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: {
      version: 1,
      guardrails: { rules: [{ name: "pii-detection", enabled: true }] },
    },
    isLoading: false,
  }),
  useCreateOrgConfig: () => ({ mutate: vi.fn() }),
}));
vi.mock("../components/GatewayAdminOnly", () => ({
  default: ({ children }) => children,
}));
vi.mock("../components/frame", () => {
  function GatewayStatusChip({ label }) {
    return <span>{label}</span>;
  }
  GatewayStatusChip.propTypes = { label: PropTypes.node };
  return {
    GatewayEmptyState: () => null,
    GatewayGrid: () => null,
    GatewayStatusChip,
  };
});
vi.mock("./ui", () => {
  function Figure({ label, value, caption }) {
    return (
      <section aria-label={label}>
        <span>{value}</span>
        <span>{caption}</span>
      </section>
    );
  }
  Figure.propTypes = {
    label: PropTypes.string,
    value: PropTypes.node,
    caption: PropTypes.node,
  };
  function SettingsPanel({ children, title, actions }) {
    return (
      <section>
        <h2>{title}</h2>
        {actions}
        {children}
      </section>
    );
  }
  SettingsPanel.propTypes = {
    children: PropTypes.node,
    title: PropTypes.node,
    actions: PropTypes.node,
  };
  return { Figure, SettingsPanel };
});
vi.mock("./orgConfigSections", () => ({ orgConfigSections: () => [] }));
vi.mock("./OrgConfigEditor", () => ({ default: () => null }));
vi.mock("./ConfigHistoryDrawer", () => ({ default: () => null }));
vi.mock("src/components/date-time/LocalDateTime", () => ({
  LocalDateTime: () => null,
}));
vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["primary", "backup"],
}));
vi.mock("./FallbackChainEditor", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeEach(() => {
  state.canEdit = false;
  state.isLoading = false;
  state.config = { guardrail_pipeline: { enabled: false } };
});
afterEach(cleanup);

describe("organization config release controls", () => {
  it("discloses that saved checks are not enforced while the node pipeline is off", () => {
    render(<OrgConfigSection />);
    const figure = screen.getByRole("region", { name: "Guardrail checks" });
    expect(figure).toHaveTextContent(
      "Not enforced: guardrails are off on this gateway",
    );
    expect(within(figure).getByText("1")).toBeInTheDocument();
  });
  it.each([true, undefined])(
    "does not claim off when the gateway reports enabled=%s",
    (enabled) => {
      state.config = { guardrail_pipeline: { enabled } };
      render(<OrgConfigSection />);
      expect(
        screen.getByRole("region", { name: "Guardrail checks" }),
      ).toHaveTextContent("1 overrides");
      expect(screen.queryByText(/Not enforced/)).toBeNull();
    },
  );
  it("does not use an old off value while the node config is loading", () => {
    state.isLoading = true;
    render(<OrgConfigSection />);
    expect(screen.queryByText(/Not enforced/)).toBeNull();
  });
  it.each([false, true])(
    "keeps disclosure usable and gates mutations for canEdit=%s",
    (canEdit) => {
      state.canEdit = canEdit;
      const change = vi.fn();
      render(
        <RoutingConfigTab
          routing={{ circuit_breaker: { enabled: true } }}
          onChange={change}
        />,
      );
      const disclosure = screen.getByRole("button", {
        name: /^Circuit Breaker/,
      });
      expect(disclosure).toBeEnabled();
      fireEvent.click(disclosure);
      const toggle = screen.getByRole("checkbox", {
        name: "Enable Circuit Breaker",
      });
      if (canEdit) expect(toggle).toBeEnabled();
      else expect(toggle).toBeDisabled();
      fireEvent.click(toggle);
      if (canEdit)
        expect(change).toHaveBeenCalledWith({
          circuit_breaker: { enabled: false },
        });
      else expect(change).not.toHaveBeenCalled();
    },
  );
});
