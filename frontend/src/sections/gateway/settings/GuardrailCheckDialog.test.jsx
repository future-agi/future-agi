import React from "react";
import { describe, it, expect, vi } from "vitest";
import {
  render,
  screen,
  fireEvent,
  within,
  userEvent,
} from "src/utils/test-utils";

import GuardrailCheckDialog from "./GuardrailCheckDialog";
import GuardrailConfigTab from "./GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({
  logger: { warn: vi.fn() },
}));

const noop = () => {};

const renderDialog = (props = {}) =>
  render(
    <GuardrailCheckDialog
      open
      onClose={noop}
      onSave={noop}
      checkName="presidio-pii"
      initialData={null}
      providerMeta={null}
      {...props}
    />,
  );

describe("GuardrailCheckDialog title", () => {
  // The slug cannot be title-cased into the right name. Two ways it breaks:
  // acronyms ("presidio-pii" -> "Presidio Pii") and vendor names that simply
  // differ from the slug ("bedrock-guardrails" is "AWS Bedrock Guardrails").
  // 15 of the 28 checks were affected, so the title must come from the label.
  it.each([
    ["presidio-pii", "Presidio PII"],
    ["pii-detection", "PII Detection"],
    ["mcp-security", "MCP Security"],
    ["crowdstrike-aidr", "CrowdStrike AIDR"],
    ["bedrock-guardrails", "AWS Bedrock Guardrails"],
    ["enkrypt-guard", "Enkrypt AI"],
  ])("uses the provider label for %s", (checkName, label) => {
    renderDialog({ checkName, providerMeta: { label } });

    expect(screen.getByText(`Configure: ${label}`)).toBeInTheDocument();
  });

  it("does not title-case the slug when a label is available", () => {
    renderDialog({
      checkName: "presidio-pii",
      providerMeta: { label: "Presidio PII" },
    });

    expect(screen.queryByText("Configure: Presidio Pii")).toBeNull();
  });

  it("falls back to the slug when the dialog opens without provider meta", () => {
    renderDialog({ checkName: "keyword-blocklist", providerMeta: null });

    expect(
      screen.getByText("Configure: Keyword Blocklist"),
    ).toBeInTheDocument();
  });
});

describe("GuardrailCheckDialog action", () => {
  // The gateway only knows block, warn and log; it ran the "mask" this
  // dialog used to offer as log, so nothing was ever masked.
  it("offers block, warn and log, and no mask", async () => {
    renderDialog();

    await userEvent.click(screen.getByRole("combobox", { name: /^Action/ }));

    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Block", "Warn", "Log"]);
  });

  it("shows a stored mask action as Log, explains it, and saves log", () => {
    const onSave = vi.fn();
    renderDialog({
      checkName: "content-moderation",
      initialData: { enabled: true, action: "mask", confidence_threshold: 0.8 },
      onSave,
    });

    expect(screen.getByRole("combobox", { name: /^Action/ })).toHaveTextContent(
      "Log",
    );
    const note = screen.getByRole("alert");
    expect(note).toHaveTextContent('saved with action "mask"');
    expect(note).toHaveTextContent("so it runs as Log");
    expect(note).toHaveTextContent("Saving stores Log");
    // Only checks with a Remediation field get pointed at it.
    expect(note).not.toHaveTextContent("Remediation");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(
      "content-moderation",
      expect.objectContaining({ action: "log" }),
    );
  });

  it("keeps a supported stored action and shows no note", () => {
    const onSave = vi.fn();
    renderDialog({ initialData: { enabled: true, action: "warn" }, onSave });

    expect(screen.queryByRole("alert")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(
      "presidio-pii",
      expect.objectContaining({ action: "warn" }),
    );
  });

  // The Rules tab reuses one dialog for every check.
  it.each([
    ["a check with a supported action", { enabled: true, action: "warn" }],
    ["an unconfigured check", null],
  ])("drops the note when reopened for %s", (_, initialData) => {
    const { rerender } = renderDialog({
      initialData: { enabled: true, action: "mask" },
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();

    rerender(
      <GuardrailCheckDialog
        open
        onClose={noop}
        onSave={noop}
        checkName="content-moderation"
        initialData={initialData}
        providerMeta={null}
      />,
    );

    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("GuardrailCheckDialog config", () => {
  it("keeps stored keys without a field and drops a cleared field", () => {
    const onSave = vi.fn();
    renderDialog({
      checkName: "hiddenlayer-guard",
      providerMeta: {
        label: "HiddenLayer",
        fields: [{ key: "model_id", label: "Model ID", type: "text" }],
      },
      initialData: {
        enabled: true,
        action: "block",
        config: { model_id: "m-1", scan_mode: "strict" },
      },
      onSave,
    });

    fireEvent.change(screen.getByLabelText("Model ID"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave.mock.calls[0][1].config).toEqual({ scan_mode: "strict" });
  });
});

describe("GuardrailConfigTab -> dialog (TH-3989 repro)", () => {
  it("opens the Presidio card with a correctly cased modal title", async () => {
    render(<GuardrailConfigTab guardrails={{ checks: {} }} onChange={noop} />);

    const card = screen.getByText("Presidio PII").closest(".MuiCard-root");
    expect(card).not.toBeNull();

    fireEvent.click(within(card).getByRole("button"));

    expect(
      await screen.findByText("Configure: Presidio PII"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Configure: Presidio Pii")).toBeNull();
  });
});
