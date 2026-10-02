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

describe("GuardrailConfigTab", () => {
  it("shows keyword input and saves keyword-blocklist words", async () => {
    const onChange = vi.fn();

    render(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={onChange} />,
    );

    const keywordBlocklistLabel = screen.getByText("Keyword Blocklist");
    const keywordBlocklistCard = keywordBlocklistLabel.closest(".MuiCard-root");

    expect(keywordBlocklistCard).not.toBeNull();

    const editButton = within(keywordBlocklistCard).getByRole("button");
    fireEvent.click(editButton);

    expect(
      await screen.findByText("Configure: Keyword Blocklist"),
    ).toBeInTheDocument();

    const blockedKeywordsInput = screen.getByPlaceholderText(
      "Enter one keyword or phrase per line",
    );

    expect(blockedKeywordsInput).toBeInTheDocument();

    fireEvent.change(blockedKeywordsInput, {
      target: { value: "alpha\nbeta, gamma\nalpha" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith({
      checks: {
        "keyword-blocklist": {
          enabled: true,
          action: "block",
          confidence_threshold: 0.8,
          config: {
            words: ["alpha", "beta", "gamma"],
          },
        },
      },
    });
  });

  it("normalizes existing checks arrays before saving new checks", async () => {
    const onChange = vi.fn();

    render(
      <GuardrailConfigTab
        guardrails={{
          checks: [
            {
              name: "pii-detector",
              enabled: true,
              action: "block",
              config: { entities: ["EMAIL_ADDRESS"] },
            },
          ],
        }}
        onChange={onChange}
      />,
    );

    const keywordBlocklistLabel = screen.getByText("Keyword Blocklist");
    const keywordBlocklistCard = keywordBlocklistLabel.closest(".MuiCard-root");

    expect(keywordBlocklistCard).not.toBeNull();

    const editButton = within(keywordBlocklistCard).getByRole("button");
    fireEvent.click(editButton);

    const blockedKeywordsInput = await screen.findByPlaceholderText(
      "Enter one keyword or phrase per line",
    );
    fireEvent.change(blockedKeywordsInput, {
      target: { value: "browser_guardrail_keyword" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls.at(-1)[0];
    expect(saved.checks["0"]).toBeUndefined();
    expect(saved.checks["pii-detection"]).toMatchObject({
      name: "pii-detector",
      _originalName: "pii-detector",
      enabled: true,
    });
    expect(saved.checks["keyword-blocklist"].config.words).toEqual([
      "browser_guardrail_keyword",
    ]);
  });
});

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
