import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import GuardrailConfigTab from "../GuardrailConfigTab";
import RuleDrawer from "../../guardrails/rules/RuleDrawer";
import { catalogEntry } from "../../guardrails/catalog";
import { formToConfig } from "../../guardrails/catalog/form-model";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("../../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true, disabledReason: null }),
}));

// The gateway's topic check reads config.allowed_topics and
// config.forbidden_topics, each a list of {name, keywords}, and its language
// check reads config.allowed_scripts, a list of lower-case script names
// (agentcc-gateway internal/guardrails/topic and language). With none set,
// either check passes every request.
async function openCard(label, guardrails = { checks: {} }) {
  const onChange = vi.fn();
  render(<GuardrailConfigTab guardrails={guardrails} onChange={onChange} />);
  const card = screen.getByText(label).closest(".MuiCard-root");
  fireEvent.click(
    within(/** @type {HTMLElement} */ (card)).getAllByRole("button")[0],
  );
  await screen.findByText(`Configure: ${label}`);
  return onChange;
}

describe("Topic Restriction and Script Detection in the Org config editor", () => {
  it("shows and resaves an existing topic byte-exact", async () => {
    const stored = {
      enabled: true,
      action: "block",
      confidence_threshold: 0.8,
      config: {
        forbidden_topics: [{ name: "weapons", keywords: ["make a bomb"] }],
      },
    };
    const onChange = await openCard("Topic Restriction", {
      checks: { "topic-restriction": stored },
    });
    expect(screen.getByLabelText(/Forbidden topics/)).toHaveValue(
      "weapons: make a bomb",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      JSON.stringify(onChange.mock.calls[0][0].checks["topic-restriction"]),
    ).toBe(JSON.stringify(stored));
  });

  it("enables a saved topic check without rewriting its topics", () => {
    const stored = {
      enabled: false,
      action: "block",
      confidence_threshold: 0.8,
      config: {
        forbidden_topics: [{ name: "billing", keywords: ["alpha,beta"] }],
      },
    };
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{ checks: { "topic-restriction": stored } }}
        onChange={onChange}
      />,
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: "Turn Topic Restriction on or off",
      }),
    );
    expect(onChange.mock.calls[0][0].checks["topic-restriction"]).toEqual({
      ...stored,
      enabled: true,
    });
  });

  it.each(["", "  ", ", ,\n,"])(
    "refuses an empty parsed blocklist: %j",
    async (value) => {
      const onChange = await openCard("Keyword Blocklist");
      fireEvent.change(screen.getByLabelText(/Blocked Keywords/), {
        target: { value },
      });
      const save = screen.getByRole("button", { name: "Save" });
      expect(save).toBeDisabled();
      fireEvent.click(save);
      expect(onChange).not.toHaveBeenCalled();
    },
  );

  it("saves named empty topics but never invents names for nameless topics", async () => {
    const onChange = await openCard("Topic Restriction");
    fireEvent.change(screen.getByLabelText(/Forbidden topics/), {
      target: { value: "billing:\n: invoice\ngambling" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      onChange.mock.calls[0][0].checks["topic-restriction"].config
        .forbidden_topics,
    ).toEqual([
      { name: "billing", keywords: ["billing"] },
      { name: "gambling", keywords: ["gambling"] },
    ]);
  });

  it("850(a): lets admins edit stored checks and set up absent checks while the node pipeline is off", async () => {
    const onChange = vi.fn();
    const guardrails = {
      checks: {
        "keyword-blocklist": {
          enabled: true,
          action: "block",
          config: { words: ["stored"] },
        },
      },
    };
    render(
      <GuardrailConfigTab
        nodePipeline={{ enabled: false }}
        guardrails={guardrails}
        onChange={onChange}
      />,
    );
    expect(onChange).not.toHaveBeenCalled();
    const saved = /** @type {HTMLElement} */ (
      screen.getByText("Keyword Blocklist").closest(".MuiCard-root")
    );
    expect(within(saved).getByText("Not enforced")).toBeInTheDocument();
    const storedSwitch = within(saved).getByRole("checkbox");
    expect(storedSwitch).toBeEnabled();
    expect(storedSwitch).toBeChecked();
    fireEvent.click(storedSwitch);
    expect(onChange).toHaveBeenCalledExactlyOnceWith({
      ...guardrails,
      checks: {
        ...guardrails.checks,
        "keyword-blocklist": {
          ...guardrails.checks["keyword-blocklist"],
          enabled: false,
        },
      },
    });
    expect(within(saved).getByRole("button", { name: "Remove" })).toBeEnabled();
    const absent = /** @type {HTMLElement} */ (
      screen.getByText("Topic Restriction").closest(".MuiCard-root")
    );
    const absentSwitch = within(absent).getByRole("checkbox");
    expect(absentSwitch).toBeEnabled();
    expect(absentSwitch).not.toBeChecked();
    expect(
      within(absent).getByRole("button", {
        name: "Configure Topic Restriction",
      }),
    ).toBeEnabled();
    onChange.mockClear();
    fireEvent.click(absentSwitch);
    expect(
      await screen.findByText("Configure: Topic Restriction"),
    ).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
  it("saves forbidden and allowed topics in the gateway's shape", async () => {
    const onChange = await openCard("Topic Restriction");
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    expect(
      screen.getByText("Add at least one forbidden or allowed topic."),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Forbidden topics/), {
      target: { value: "Legal: lawsuit, attorney" },
    });
    fireEvent.change(screen.getByLabelText(/Allowed topics/), {
      target: { value: "Billing: invoice, refund" },
    });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(
      onChange.mock.calls[0][0].checks["topic-restriction"].config,
    ).toEqual({
      forbidden_topics: [{ name: "Legal", keywords: ["lawsuit", "attorney"] }],
      allowed_topics: [{ name: "Billing", keywords: ["invoice", "refund"] }],
    });
  });

  it("names the check by what it judges: scripts, not languages", () => {
    render(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={vi.fn()} />,
    );
    expect(screen.getByText("Script Detection")).toBeInTheDocument();
    expect(screen.queryByText("Language Detection")).toBeNull();
  });

  it("saves the allowed scripts, and refuses an empty list", async () => {
    const onChange = await openCard("Script Detection");
    const save = screen.getByRole("button", { name: "Save" });
    // A new check starts on Latin.
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(
      onChange.mock.calls[0][0].checks["language-detection"].config,
    ).toEqual({ allowed_scripts: ["latin"] });
  });

  it("refuses to save Script Detection with no allowed script", async () => {
    await openCard("Script Detection", {
      checks: {
        "language-detection": {
          enabled: false,
          action: "block",
          config: { allowed_scripts: [] },
        },
      },
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(
      screen.getByText("Choose at least one allowed script."),
    ).toBeInTheDocument();
  });

  it("opens the settings instead of turning on a topic check with no topics", async () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={onChange} />,
    );
    const card = screen.getByText("Topic Restriction").closest(".MuiCard-root");
    fireEvent.click(
      within(/** @type {HTMLElement} */ (card)).getByRole("checkbox"),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(
      await screen.findByText("Configure: Topic Restriction"),
    ).toBeInTheDocument();
  });

  it("the Rules drawer refuses to add a topic check with no topics, and says why", async () => {
    render(
      <RuleDrawer
        open
        meta={catalogEntry("topic-restriction")}
        row={null}
        onClose={() => {}}
        onSave={() => {}}
      />,
    );
    const drawer = await screen.findByRole("dialog", {
      name: "Topic Restriction",
    });
    expect(
      within(drawer).getByRole("button", { name: "Add check" }),
    ).toBeDisabled();
    expect(
      within(drawer).getByText("Add at least one forbidden or allowed topic."),
    ).toBeInTheDocument();
  });

  it("the Rules drawer stores topics in the same shape", () => {
    expect(
      formToConfig(
        catalogEntry("topic-restriction"),
        { forbidden_topics: "Legal: lawsuit", allowed_topics: "" },
        {},
      ),
    ).toEqual({ forbidden_topics: [{ name: "Legal", keywords: ["lawsuit"] }] });
  });
});
