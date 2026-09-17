import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

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
