import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

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

  it("saves Lakera Guard v2 detector categories and project id", async () => {
    const onChange = vi.fn();

    render(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={onChange} />,
    );

    const lakeraCard = screen
      .getByText("Lakera Guard")
      .closest(".MuiCard-root");
    fireEvent.click(within(lakeraCard).getByRole("button"));

    expect(
      await screen.findByText("Configure: Lakera Guard"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Categories" }),
    ).toHaveAccessibleDescription(
      /^Only these Lakera detector groups can block/,
    );

    fireEvent.change(screen.getByLabelText(/API Key/), {
      target: { value: "lk-test" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("Optional Lakera project ID"),
      {
        target: { value: " project-123 " },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledWith({
      checks: {
        "lakera-guard": {
          enabled: true,
          action: "block",
          confidence_threshold: 0.8,
          provider: "lakera",
          config: {
            api_key: "lk-test",
            endpoint: "https://api.lakera.ai/v2/guard",
            project_id: "project-123",
            categories: ["prompt_attack", "moderated_content"],
          },
        },
      },
    });
  });

  it("opens a Lakera check saved with v1 categories as their v2 groups", async () => {
    const onChange = vi.fn();

    render(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "lakera-guard": {
              enabled: true,
              action: "block",
              confidence_threshold: 0.8,
              provider: "lakera",
              config: {
                api_key: "lk-test",
                endpoint: "https://api.lakera.ai/v2/guard",
                // The defaults the dashboard saved before the v2 options.
                categories: [
                  "prompt_injection",
                  "jailbreak",
                  "harmful_content",
                ],
              },
            },
          },
        }}
        onChange={onChange}
      />,
    );

    // A stored check also has a Reset button; edit is first.
    const lakeraCard = screen
      .getByText("Lakera Guard")
      .closest(".MuiCard-root");
    fireEvent.click(within(lakeraCard).getAllByRole("button")[0]);

    const dialog = await screen.findByRole("dialog");
    const chips = [...dialog.querySelectorAll(".MuiChip-label")].map(
      (chip) => chip.textContent,
    );
    expect(chips).toEqual(["prompt_attack", "moderated_content"]);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(
      onChange.mock.calls.at(-1)[0].checks["lakera-guard"].config.categories,
    ).toEqual(["prompt_attack", "moderated_content"]);
  });
});
