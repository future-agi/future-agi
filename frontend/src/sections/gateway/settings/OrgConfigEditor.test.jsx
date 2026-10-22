import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import OrgConfigEditor from "./OrgConfigEditor";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

vi.mock("./ProviderConfigTab", () => ({
  default: () => <div>Providers tab</div>,
}));

vi.mock("./GuardrailConfigTab", () => ({
  default: () => <div>Guardrails tab</div>,
}));

vi.mock("./RoutingConfigTab", () => ({
  default: () => <div>Routing tab</div>,
}));

vi.mock("./CacheConfigTab", () => ({
  default: () => <div>Cache tab</div>,
}));

vi.mock("./RateLimitingConfigTab", () => ({
  default: () => <div>Rate limiting tab</div>,
}));

vi.mock("./BudgetsConfigTab", () => ({
  default: () => <div>Budgets tab</div>,
}));

vi.mock("./SecurityConfigTab", () => ({
  default: () => <div>Security tab</div>,
}));

vi.mock("./MCPConfigTab", () => ({
  default: () => <div>MCP tab</div>,
}));

vi.mock("./A2AConfigTab", () => ({
  default: () => <div>A2A tab</div>,
}));

vi.mock("./AuditConfigTab", () => ({
  default: () => <div>Audit tab</div>,
}));

vi.mock("./ModelDatabaseConfigTab", () => ({
  default: () => <div>Model database tab</div>,
}));

vi.mock("./ModelMapConfigTab", () => ({
  default: () => <div>Model map tab</div>,
}));

// The role check: every member opens the editor to read
// the config; Save & Activate is Owner or Admin only.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

describe("OrgConfigEditor, Owner or Admin only", () => {
  afterEach(() => {
    mockOrgLevel = 8;
  });

  it.each(NOT_ADMIN_LEVELS)(
    "%s reads the config with Save & Activate disabled",
    (_who, level) => {
      mockOrgLevel = level;
      render(
        <OrgConfigEditor
          open
          onClose={vi.fn()}
          onSave={vi.fn()}
          initialConfig={{ providers: {}, alerting: { enabled: true } }}
          isSaving={false}
          defaultTab={7}
        />,
      );
      expect(screen.getByText("Notification Channels")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
      expectAdminOnly(screen.getByRole("button", { name: "Save & Activate" }));
    },
  );

  it.each(ADMIN_LEVELS)("%s can save", async (_who, level) => {
    mockOrgLevel = level;
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <OrgConfigEditor
        open
        onClose={vi.fn()}
        onSave={onSave}
        initialConfig={{ providers: {}, alerting: { enabled: true } }}
        isSaving={false}
        defaultTab={7}
      />,
    );
    const save = screen.getByRole("button", { name: "Save & Activate" });
    expectEditable(save);
    await user.click(save);
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});

describe("OrgConfigEditor", () => {
  it("preserves alerting draft state when initialConfig changes after enabling alerting", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSave = vi.fn();

    const initialConfig = {
      providers: {},
      alerting: {},
    };

    const { rerender } = render(
      <OrgConfigEditor
        open
        onClose={onClose}
        onSave={onSave}
        initialConfig={initialConfig}
        isSaving={false}
        defaultTab={7}
      />,
    );

    const alertingToggle = screen.getByRole("checkbox", {
      name: /enable per-org alerting/i,
    });

    expect(alertingToggle).not.toBeChecked();

    await user.click(alertingToggle);

    expect(alertingToggle).toBeChecked();
    expect(screen.getByText("Notification Channels")).toBeInTheDocument();

    rerender(
      <OrgConfigEditor
        open
        onClose={onClose}
        onSave={onSave}
        initialConfig={{
          ...initialConfig,
          _refetchVersion: 1,
        }}
        isSaving={false}
        defaultTab={7}
      />,
    );

    expect(
      screen.getByRole("checkbox", { name: /enable per-org alerting/i }),
    ).toBeChecked();
    expect(screen.getByText("Notification Channels")).toBeInTheDocument();
  });
});
