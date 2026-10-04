import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import GuardrailConfigTab from "./GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({
  logger: {
    warn: vi.fn(),
  },
}));

describe("GuardrailConfigTab tool permissions", () => {
  it("keeps the saved tool-permissions mode apart from the execution mode", async () => {
    const onChange = vi.fn();

    render(
      <GuardrailConfigTab
        guardrails={{
          rules: [
            {
              name: "tool-permissions",
              mode: "sync",
              stage: "pre",
              action: "block",
              enabled: true,
              config: {
                mode: "allowlist",
                tools: "file_*",
                apply_to: "request",
              },
            },
          ],
        }}
        onChange={onChange}
      />,
    );

    const card = screen.getByText("Tool Permissions").closest(".MuiCard-root");
    const editButton = within(card)
      .getAllByRole("button")
      .find((button) => button.textContent !== "Reset");
    fireEvent.click(editButton);

    expect(
      await screen.findByText("Configure: Tool Permissions"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const check = onChange.mock.calls[0][0].checks["tool-permissions"];
    expect(check.config.mode).toBe("allowlist");
    expect(check.mode).toBe("sync");
  });
});
