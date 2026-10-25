import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import AlertingConfigTab from "./AlertingConfigTab";

// An alert channel URL that carries a credential (a
// Slack incoming webhook, a token in the path or query) is a write-only
// secret. Every org-config read returns the mask for it, never the URL. The
// Alerting tab shows a stored one as dots with Replace, never as an editable
// value, so a save sends the stored URL or the new value typed after
// Replace.
const MASK = "__encrypted__0123456789abcdef";

function alerting(channels) {
  return { enabled: true, rules: [], channels };
}

describe("AlertingConfigTab with a stored credential URL", () => {
  it("shows the stored URL as dots with Replace, and no Remove", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const config = alerting([
      { name: "ops-slack", type: "slack", url: MASK },
      { name: "plain", type: "webhook", url: "https://hooks.example/alerts" },
    ]);
    const { container } = render(
      <AlertingConfigTab alerting={config} onChange={onChange} />,
    );

    const stored = screen.getAllByLabelText("URL")[0];
    expect(stored).toHaveAttribute("readonly");
    expect(stored.value).toMatch(/•+/);
    for (const input of container.querySelectorAll("input, textarea")) {
      expect(input.value).not.toContain("__encrypted__");
    }
    expect(container.textContent).not.toContain("__encrypted__");
    // A channel's URL is required for it to deliver, so it is replaced, not
    // removed (removing the channel is the delete button).
    expect(screen.queryByRole("button", { name: /^remove$/i })).toBeNull();

    // A URL that carries no credential reads and edits as plain text.
    expect(screen.getAllByLabelText("URL")[1].value).toBe(
      "https://hooks.example/alerts",
    );

    await user.click(screen.getByRole("button", { name: /replace/i }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...config,
      channels: [{ ...config.channels[0], url: "" }, config.channels[1]],
    });
  });

  it("a replaced URL is typed as a new value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const config = alerting([{ name: "ops-slack", type: "slack", url: "" }]);
    render(<AlertingConfigTab alerting={config} onChange={onChange} />);

    const input = screen.getByLabelText("URL");
    expect(input).not.toHaveAttribute("readonly");
    await user.type(input, "h");
    expect(onChange).toHaveBeenLastCalledWith({
      ...config,
      channels: [{ ...config.channels[0], url: "h" }],
    });
  });
});
