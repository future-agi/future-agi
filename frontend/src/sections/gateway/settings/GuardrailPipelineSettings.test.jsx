import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import GuardrailConfigTab from "./GuardrailConfigTab";

describe("guardrail tenant failure policy", () => {
  it.each([{}, { fail_open: false }, { fail_open: "false" }, { failOpen: "false" }])(
    "does not opt an organization in from %j",
    (settings) => {
      render(<GuardrailConfigTab guardrails={settings} onChange={vi.fn()} />);
      expect(screen.getByRole("checkbox", { name: "Fail Open" })).not.toBeChecked();
    },
  );

  it.each([{ fail_open: true }, { fail_open: "true" }, { failOpen: true }])(
    "shows an explicit opt-in from %j",
    (settings) => {
      render(<GuardrailConfigTab guardrails={settings} onChange={vi.fn()} />);
      expect(screen.getByRole("checkbox", { name: "Fail Open" })).toBeChecked();
    },
  );

  it("writes a boolean only after the owner turns Fail Open on", () => {
    const onChange = vi.fn();
    render(<GuardrailConfigTab guardrails={{ checks: {} }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Fail Open" }));
    expect(onChange).toHaveBeenCalledWith({ checks: {}, fail_open: true });
  });
});
