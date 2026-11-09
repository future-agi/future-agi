import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import { renderPage } from "./helpers";
import { catalogEntry, scoresOneOnMatch } from "../catalog";
import RuleDrawer from "../rules/RuleDrawer";
import GuardrailCheckDialog from "../../settings/GuardrailCheckDialog";
import GuardrailConfigTab from "../../settings/GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

// The gateway counts a match when its score is above the threshold. Topic
// Restriction and Language Detection always score 1.0 on a match; Keyword
// Blocklist, PII Detection and Secret Detection score 1.0 on their first
// match unless max_matches, max_entities or max_secrets is set above 1. For
// those a threshold below 1.0 changes nothing and 1.0 turns the check off.
describe("The threshold is offered only where it changes what a check does", () => {
  it.each([
    ["keyword-blocklist", false],
    ["topic-restriction", false],
    ["language-detection", false],
    ["pii-detection", false],
    ["secret-detection", false],
    ["prompt-injection", true],
    ["content-moderation", true],
    ["hallucination-detection", true],
    ["data-leakage-prevention", true],
    ["system-prompt-protection", true],
  ])(
    "offers a dialog threshold for %s only when meaningful",
    (name, offered) => {
      renderPage(
        <GuardrailCheckDialog
          open
          onClose={() => {}}
          onSave={() => {}}
          checkName={name}
          initialData={null}
          providerMeta={{ name, label: name }}
        />,
      );
      expect(Boolean(screen.queryByText(/Confidence Threshold/))).toBe(offered);
    },
  );

  it("knows which checks score 1.0 on a match", () => {
    expect(scoresOneOnMatch("keyword-blocklist", null)).toBe(true);
    expect(scoresOneOnMatch("topic-restriction", {})).toBe(true);
    expect(scoresOneOnMatch("language-detection", {})).toBe(true);
    expect(scoresOneOnMatch("secret-detection", { config: {} })).toBe(true);
    expect(
      scoresOneOnMatch("pii-detection", { config: { max_entities: 3 } }),
    ).toBe(false);
    expect(scoresOneOnMatch("prompt-injection", null)).toBe(false);
    expect(scoresOneOnMatch("content-moderation", null)).toBe(false);
  });

  it("the rule drawer shows no threshold for Keyword Blocklist", async () => {
    renderPage(
      <RuleDrawer
        open
        meta={catalogEntry("keyword-blocklist")}
        row={null}
        onClose={() => {}}
        onSave={() => {}}
      />,
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Keyword Blocklist",
    });
    expect(within(drawer).queryByText(/Match threshold/)).toBeNull();
    expect(within(drawer).queryByRole("slider")).toBeNull();
  });

  it("the rule drawer keeps the threshold for Prompt Injection", async () => {
    renderPage(
      <RuleDrawer
        open
        meta={catalogEntry("prompt-injection")}
        row={null}
        onClose={() => {}}
        onSave={() => {}}
      />,
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Prompt Injection",
    });
    expect(within(drawer).getByRole("slider")).toBeInTheDocument();
  });

  it("the Configure dialog shows no threshold for Keyword Blocklist, and keeps it for Content Moderation", () => {
    const { unmount } = renderPage(
      <GuardrailCheckDialog
        open
        onClose={() => {}}
        onSave={() => {}}
        checkName="keyword-blocklist"
        initialData={null}
        providerMeta={{ name: "keyword-blocklist", label: "Keyword Blocklist" }}
      />,
    );
    expect(screen.queryByText(/Confidence Threshold/)).toBeNull();
    unmount();
    renderPage(
      <GuardrailCheckDialog
        open
        onClose={() => {}}
        onSave={() => {}}
        checkName="content-moderation"
        initialData={null}
        providerMeta={{
          name: "content-moderation",
          label: "Content Moderation",
        }}
      />,
    );
    expect(screen.getByText(/Confidence Threshold/)).toBeInTheDocument();
  });

  it("the Guardrails tab card names no threshold for a stored Keyword Blocklist", () => {
    renderPage(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "keyword-blocklist": {
              enabled: true,
              action: "block",
              confidence_threshold: 0.8,
              config: { words: ["x"] },
            },
          },
        }}
        onChange={() => {}}
      />,
    );
    const card = screen.getByText("Keyword Blocklist").closest(".MuiCard-root");
    expect(
      within(/** @type {HTMLElement} */ (card)).getByText("Action: block"),
    ).toBeInTheDocument();
    expect(
      within(/** @type {HTMLElement} */ (card)).queryByText(/Threshold/),
    ).toBeNull();
  });
});
