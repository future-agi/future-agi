import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import GuardrailConfigTab from "../GuardrailConfigTab";
import {
  buildConfigPatch,
  guardrailsForSave,
} from "../../hooks/useConfigPatch";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
// The Org config editor's switches are Owner or Admin only
// (GatewayWriteControls, useGatewayAdmin): this case edits as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// A save of anything else in the Org config editor's Guardrails tab sends
// the stored model-request checks (MCP Security, Tool Permissions) back
// exactly as stored, MCP Security's stored-only Allowed Servers included:
// the tab offers neither check (noToolOrMcpChecks.test.jsx), and neither
// is rewritten by a whole-section save (useConfigPatch guardrailsForSave).

const card = (label) =>
  screen
    .getAllByText(label)
    .map((n) => n.closest(".MuiCard-root"))
    .find(Boolean);

describe("a save of another check keeps the stored MCP rules as they are", () => {
  const saved = {
    rules: [
      // Saved without a stage, as the Guardrails page saves a rule (the
      // gateway never reads one).
      {
        name: "mcp-security",
        mode: "sync",
        action: "block",
        threshold: 0.5,
        enabled: true,
        config: {
          allowed_servers: " walk-echo ",
          blocked_tools: "db_write",
          provider: "mcp_security",
        },
      },
      {
        name: "tool-permissions",
        mode: "sync",
        action: "block",
        threshold: 1,
        enabled: true,
        config: {
          mode: "denylist",
          tools: "file_*",
          provider: "tool_permission",
        },
      },
      {
        name: "keyword-blocklist",
        stage: "pre",
        mode: "sync",
        action: "block",
        threshold: 0.8,
        enabled: true,
        config: { words: ["project-zeus"] },
      },
    ],
  };

  it("switching another check off sends the MCP rules as stored", () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={saved}
        saved={saved}
        onChange={onChange}
      />,
    );
    fireEvent.click(within(card("Keyword Blocklist")).getByRole("checkbox"));
    const toSave = guardrailsForSave(onChange.mock.calls[0][0], saved);
    const patch = buildConfigPatch(
      { guardrails: saved },
      { guardrails: toSave },
      ["guardrails"],
    );
    const rules = patch.guardrails.rules;
    expect(JSON.stringify(rules.find((r) => r.name === "mcp-security"))).toBe(
      JSON.stringify(saved.rules[0]),
    );
    expect(
      JSON.stringify(rules.find((r) => r.name === "tool-permissions")),
    ).toBe(JSON.stringify(saved.rules[1]));
    expect(rules.find((r) => r.name === "keyword-blocklist").enabled).toBe(
      false,
    );
  });
});
