import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import GuardrailConfigTab from "./GuardrailConfigTab";
import { buildConfigPatch, guardrailsForSave } from "../hooks/useConfigPatch";

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

// The gateway runs a check of a section stored as a checks map that carries
// no action as log, and one with no threshold at 0 (release notes N7;
// TestPlugin_OrgCheckWithoutAnActionLogs and
// TestPlugin_OrgCheckWithoutAThresholdTriggersAboveZero). The Rules tab shows
// such a check as the gateway runs it, and its dialog opens on those values,
// so choosing Block is an edit, and is saved. A section stored as rules or as
// the policy sync's list gets block and 0.8 from the config push for a check
// without them, and is shown so.
describe("GuardrailConfigTab, a check stored without an action or a threshold", () => {
  const savedMap = {
    checks: {
      "data-leakage-prevention": { enabled: true },
      "pii-detection": {
        enabled: true,
        action: "block",
        confidence_threshold: 0.8,
      },
    },
  };

  const leakageCard = () =>
    screen.getAllByText(/Data Leakage/i)[0].closest(".MuiCard-root");

  const shownOnCard = () =>
    within(leakageCard())
      .queryAllByText(/^(Action|Threshold):/)
      .map((n) => n.textContent);

  async function openLeakage(onChange, saved = savedMap, guardrails = saved) {
    render(
      <GuardrailConfigTab
        guardrails={guardrails}
        saved={saved}
        onChange={onChange}
      />,
    );
    fireEvent.click(within(leakageCard()).getAllByRole("button")[0]);
    return screen.findByRole("dialog");
  }

  const actionShown = () =>
    within(screen.getByRole("dialog")).getByRole("combobox", {
      name: /Action/,
    }).textContent;

  async function pickAction(name) {
    fireEvent.mouseDown(
      within(screen.getByRole("dialog")).getByRole("combobox", {
        name: /Action/,
      }),
    );
    fireEvent.click(await screen.findByRole("option", { name }));
  }

  function saveAndPatch(onChange, saved = savedMap) {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const edited = onChange.mock.calls.at(-1)[0];
    const toSave = guardrailsForSave(edited, saved);
    return {
      check: toSave.checks["data-leakage-prevention"],
      patch: buildConfigPatch({ guardrails: saved }, { guardrails: toSave }, [
        "guardrails",
      ]),
    };
  }

  it("shows a map check as the gateway runs it: log, at threshold 0", () => {
    render(
      <GuardrailConfigTab
        guardrails={savedMap}
        saved={savedMap}
        onChange={vi.fn()}
      />,
    );

    expect(shownOnCard()).toEqual(["Action: log", "Threshold: 0.00"]);
  });

  it("opens the dialog on log and 0, so choosing Block saves block", async () => {
    const onChange = vi.fn();
    await openLeakage(onChange);

    expect(actionShown()).toBe("Log");
    expect(screen.getByText("Confidence Threshold: 0.00")).toBeInTheDocument();

    await pickAction("Block");
    const { check, patch } = saveAndPatch(onChange);
    expect(check).toEqual({ enabled: true, action: "block" });
    expect(patch).toEqual({
      guardrails: {
        checks: { "data-leakage-prevention": { action: "block" } },
      },
    });
  });

  it("saves Block chosen after Warn in one session", async () => {
    const onChange = vi.fn();
    await openLeakage(onChange);

    await pickAction("Warn");
    await pickAction("Block");
    expect(saveAndPatch(onChange).check).toEqual({
      enabled: true,
      action: "block",
    });
  });

  it("saves nothing for a map check opened and saved unchanged", async () => {
    const onChange = vi.fn();
    await openLeakage(onChange);

    const { check, patch } = saveAndPatch(onChange);
    expect(check).toEqual({ enabled: true });
    expect(patch).toEqual({});
  });

  it("shows block and 0.80 for a check of a section stored as the policy sync's list", async () => {
    // A list check without an action or a threshold runs with block and 0.8
    // (the config push's defaults for the list), also once an edit turned
    // the draft into a map.
    const savedList = {
      checks: [{ name: "data-leakage-prevention", enabled: true }],
    };
    const draftMap = {
      checks: {
        "data-leakage-prevention": {
          name: "data-leakage-prevention",
          enabled: true,
          _originalName: "data-leakage-prevention",
        },
      },
    };
    render(
      <GuardrailConfigTab
        guardrails={draftMap}
        saved={savedList}
        onChange={vi.fn()}
      />,
    );

    expect(shownOnCard()).toEqual(["Action: block", "Threshold: 0.80"]);
  });
});
