import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import GuardrailConfigTab from "./GuardrailConfigTab";

// Organization config writes are Owner or Admin only; render as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));

// A gateway whose own Fail Open is off: an organization that has not set
// Fail Open shows the gateway's setting.
const NODE_FAIL_OPEN_OFF = { enabled: true, fail_open: false };

describe("guardrail tenant failure policy", () => {
  it.each([
    {},
    { fail_open: false },
    { fail_open: "false" },
    { failOpen: "false" },
  ])("does not opt an organization in from %j", (settings) => {
    render(
      <GuardrailConfigTab
        guardrails={settings}
        onChange={vi.fn()}
        nodePipeline={NODE_FAIL_OPEN_OFF}
      />,
    );
    expect(
      screen.getByRole("checkbox", { name: "Fail Open" }),
    ).not.toBeChecked();
  });

  it.each([{ fail_open: true }, { fail_open: "true" }, { failOpen: true }])(
    "shows an explicit opt-in from %j",
    (settings) => {
      render(<GuardrailConfigTab guardrails={settings} onChange={vi.fn()} />);
      expect(screen.getByRole("checkbox", { name: "Fail Open" })).toBeChecked();
    },
  );

  it("writes a boolean only after the owner turns Fail Open on", () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{ checks: {} }}
        onChange={onChange}
        nodePipeline={NODE_FAIL_OPEN_OFF}
      />,
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "Fail Open" }));
    expect(onChange).toHaveBeenCalledWith({ checks: {}, fail_open: true });
  });
});
