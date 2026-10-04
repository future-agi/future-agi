import React from "react";
import { describe, it, expect, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  within,
  userEvent,
} from "src/utils/test-utils";

import GuardrailConfigTab from "./GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({
  logger: {
    warn: vi.fn(),
  },
}));

// A stored check has both an edit button and a Reset button; edit is first.
const openCustomCheck = (label) => {
  const card = screen.getByText(label).closest(".MuiCard-root");
  fireEvent.click(within(card).getAllByRole("button")[0]);
};

describe("GuardrailConfigTab PII remediation", () => {
  it("saves the remediation alongside the stored PII config", async () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "pii-detection": {
              enabled: true,
              action: "log",
              confidence_threshold: 0.8,
              config: { entities: ["email"], max_entities: 3 },
            },
          },
        }}
        onChange={onChange}
      />,
    );

    openCustomCheck("PII Detection");
    // The help text steers Remediation away from Block and says a short
    // value's hash can be reversed.
    expect(
      await screen.findByText(/Use them with the Warn or Log action/),
    ).toHaveTextContent("short values such as SSNs can be recovered from it");
    await userEvent.click(
      await screen.findByRole("combobox", { name: /Remediation/ }),
    );
    // Remediation "block" only detects, so it must not read as Block.
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["None (detect only)", "Mask", "Redact", "Hash"]);
    await userEvent.click(screen.getByRole("option", { name: "Mask" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls.at(-1)[0].checks["pii-detection"];
    expect(saved.action).toBe("log");
    expect(saved.config).toEqual({
      entities: ["email"],
      max_entities: 3,
      remediation: "mask",
    });
  });

  it("points a PII check stored with action mask to Remediation", async () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: { "pii-detection": { enabled: true, action: "mask" } },
        }}
        onChange={onChange}
      />,
    );

    // The card must not read as if PII were being masked.
    expect(screen.getByText("Action: mask (runs as log)")).toBeInTheDocument();

    openCustomCheck("PII Detection");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "To mask PII, set Remediation below.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Saving keeps what the gateway already did: log, and no rewriting.
    const saved = onChange.mock.calls.at(-1)[0].checks["pii-detection"];
    expect(saved.action).toBe("log");
    expect(saved.config).toEqual({ remediation: "block" });
  });
});
