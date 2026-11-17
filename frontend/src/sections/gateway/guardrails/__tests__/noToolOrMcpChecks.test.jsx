import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";

import { renderPage } from "./helpers";
import AddCheckDialog from "../rules/AddCheckDialog";
import { CATALOG } from "../catalog";
import GuardrailConfigTab, {
  GUARDRAIL_CHECKS,
  MODEL_GUARDRAIL_CHECKS,
} from "../../settings/GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true, disabledReason: null }),
}));

const HIDDEN = ["MCP Security", "Tool Permissions"];

describe("No check catalog offers tool or MCP checks", () => {
  it("Add a check lists neither", () => {
    renderPage(
      <AddCheckDialog
        open
        configured={new Set()}
        onClose={() => {}}
        onPick={() => {}}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Add a check" });
    for (const label of HIDDEN) {
      expect(within(dialog).queryByText(label)).toBeNull();
    }
    expect(within(dialog).queryByText(/Model Context Protocol/)).toBeNull();
  });

  it("the Org config Guardrails tab lists neither", () => {
    renderPage(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={() => {}} />,
    );
    for (const label of HIDDEN) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });

  it("no check offers an Allowed Servers field", () => {
    const fields = [
      ...CATALOG,
      ...GUARDRAIL_CHECKS,
      ...MODEL_GUARDRAIL_CHECKS,
    ].flatMap((check) =>
      "fields" in check ? (check.fields || []).map((field) => field.key) : [],
    );
    expect(fields).not.toContain("allowed_servers");
  });

  it("keeps a stored MCP Security check unchanged when another check is edited", () => {
    const stored = {
      enabled: true,
      action: "block",
      provider: "mcp_security",
      config: { allowed_servers: "a,b", blocked_tools: "rm" },
    };
    const onChange = vi.fn();
    renderPage(
      <GuardrailConfigTab
        guardrails={{ checks: { "mcp-security": stored } }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText("PII Detection").closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getByRole("checkbox"),
    );
    const next = onChange.mock.calls.at(-1)[0];
    expect(next.checks["mcp-security"]).toEqual(stored);
    expect(next.checks["pii-detection"].enabled).toBe(true);
  });
});
