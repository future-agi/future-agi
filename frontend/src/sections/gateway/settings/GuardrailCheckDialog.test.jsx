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

const choose = (label, option) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: option }));
};

const enableReflection = () => {
  choose("Check stage", "Response");
  choose("When a response is blocked", "Retry with feedback");
};

const savedReflection = {
  stage: "post",
  action: "block",
  config: {
    stage: "post",
    on_block: "reflect",
    reflect_max_attempts: 2,
    reflect_debug: true,
  },
};

describe("Guardrail retry controls", () => {
  it("keeps legacy post checks post without injecting defaults on save", () => {
    const onSave = vi.fn();
    renderDialog({
      onSave,
      checkName: "data-leakage-prevention",
      initialData: { stage: "pre", config: { protected_terms: ["private"] } },
    });
    expect(
      screen.getByRole("combobox", { name: "Check stage" }),
    ).toHaveTextContent("Response");
    expect(
      screen.getByRole("combobox", { name: "When a response is blocked" }),
    ).toHaveTextContent("Reject response");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1].config).toEqual({
      protected_terms: ["private"],
    });
  });

  it("saves explicit opt-in with bounded defaults and the buffered-stream notice", () => {
    const onSave = vi.fn();
    renderDialog({ onSave, checkName: "keyword-blocklist" });
    expect(
      screen.queryByRole("combobox", { name: "When a response is blocked" }),
    ).toBeNull();
    enableReflection();
    expect(
      screen.getByRole("spinbutton", { name: "Additional attempts" }),
    ).toHaveValue(1);
    expect(
      screen.getByText(/Streaming responses are buffered/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Blocked tool calls, reasoning and multi-choice/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1]).toMatchObject({
      stage: "post",
      config: {
        stage: "post",
        on_block: "reflect",
        reflect_max_attempts: 1,
        reflect_debug: false,
      },
    });
  });

  it.each(["", "0", "6", "1.5", "-1"])(
    "rejects invalid attempt count %s instead of clamping",
    (value) => {
      const onSave = vi.fn();
      renderDialog({ onSave, initialData: savedReflection });
      fireEvent.change(
        screen.getByRole("spinbutton", { name: "Additional attempts" }),
        { target: { value } },
      );
      expect(
        screen.getByText("Enter a whole number from 1 to 5."),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(onSave).not.toHaveBeenCalled();
    },
  );

  it("preserves unknown config, provider mode and encrypted credentials on retry edits", () => {
    const onSave = vi.fn();
    renderDialog({
      onSave,
      initialData: {
        ...savedReflection,
        config: {
          ...savedReflection.config,
          api_key: "__encrypted__",
          mode: "provider-mode",
          custom_option: { keep: true },
        },
      },
      providerMeta: {
        fields: [{ key: "api_key", label: "API key", type: "password" }],
      },
    });
    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Additional attempts" }),
      { target: { value: "5" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1].config).toEqual({
      ...savedReflection.config,
      reflect_max_attempts: 5,
      api_key: "__encrypted__",
      mode: "provider-mode",
      custom_option: { keep: true },
    });
  });

  it("keeps provider mode distinct from the rule execution mode", () => {
    const onSave = vi.fn();
    renderDialog({
      onSave,
      checkName: "tool-permissions",
      initialData: {
        ...savedReflection,
        mode: "sync",
        config: { ...savedReflection.config, mode: "allowlist" },
      },
      providerMeta: {
        fields: [
          {
            key: "mode",
            label: "Provider mode",
            type: "select",
            options: ["allowlist", "denylist"],
          },
        ],
      },
    });
    expect(
      screen.getByRole("combobox", { name: "Provider mode" }),
    ).toHaveTextContent("allowlist");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1].config.mode).toBe("allowlist");
    expect(onSave.mock.calls[0][1].config.on_block).toBe("reflect");
  });

  it("turns off response retries when tool inspection changes to requests only", () => {
    const onSave = vi.fn();
    renderDialog({
      onSave,
      checkName: "tool-permissions",
      initialData: {
        ...savedReflection,
        config: { ...savedReflection.config, apply_to: "both" },
      },
      providerMeta: {
        provider: "tool_permissions",
        fields: [
          {
            key: "apply_to",
            label: "Apply to",
            type: "select",
            options: ["request", "response", "both"],
          },
        ],
      },
    });
    choose("Apply to", "request");
    expect(
      screen.getByRole("combobox", { name: "Check stage" }),
    ).toHaveTextContent("Request");
    expect(
      screen.queryByRole("spinbutton", { name: "Additional attempts" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1].config).toEqual({
      stage: "pre",
      on_block: "reject",
      apply_to: "request",
    });
  });

  it("keeps an inferred response stage when switching from retry to reject", () => {
    const onSave = vi.fn();
    renderDialog({
      onSave,
      checkName: "keyword-blocklist",
      initialData: {
        stage: "post",
        action: "block",
        config: { on_block: "reflect", words: ["unsafe"] },
      },
    });
    expect(
      screen.getByRole("combobox", { name: "Check stage" }),
    ).toHaveTextContent("Response");
    choose("When a response is blocked", "Reject response");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1]).toMatchObject({
      stage: "post",
      config: { stage: "post", on_block: "reject", words: ["unsafe"] },
    });
  });

  it("does not resurrect credentials explicitly cleared during a retry edit", () => {
    const onSave = vi.fn();
    renderDialog({
      onSave,
      initialData: {
        ...savedReflection,
        config: { ...savedReflection.config, api_key: "__encrypted__" },
      },
      providerMeta: {
        fields: [{ key: "api_key", label: "API key", type: "password" }],
      },
    });
    fireEvent.focus(screen.getByLabelText("API key"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave.mock.calls[0][1].config).not.toHaveProperty("api_key");
    expect(onSave.mock.calls[0][1].config.on_block).toBe("reflect");
  });

  it.each([
    ["Action", "Warn"],
    ["Check stage", "Request"],
  ])("disables hidden retry settings when %s changes", (label, option) => {
    const onSave = vi.fn();
    renderDialog({ onSave, initialData: savedReflection });
    choose(label, option);
    expect(
      screen.queryByRole("spinbutton", { name: "Additional attempts" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const config = onSave.mock.calls[0][1].config;
    expect(config.on_block).toBe("reject");
    expect(config).not.toHaveProperty("reflect_max_attempts");
    expect(config).not.toHaveProperty("reflect_debug");
  });

  it.each([
    "prompt-injection",
    "topic-restriction",
    "language-detection",
    "system-prompt-protection",
  ])("does not offer response checks for request-only %s", (checkName) => {
    renderDialog({ checkName });
    expect(
      screen.getByText("This check inspects requests only."),
    ).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Check stage" }));
    expect(screen.getByRole("option", { name: "Response" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("does not offer retries for asynchronous checks", () => {
    renderDialog({
      initialData: { stage: "post", mode: "async", config: { stage: "post" } },
    });
    expect(
      screen.queryByRole("combobox", { name: "When a response is blocked" }),
    ).toBeNull();
  });

  it("round-trips retry controls through a real check card", async () => {
    const onChange = vi.fn();
    render(
      <GuardrailConfigTab
        guardrails={{
          checks: {
            "keyword-blocklist": {
              ...savedReflection,
              config: { ...savedReflection.config, words: ["unsafe"] },
            },
          },
        }}
        onChange={onChange}
      />,
    );
    const card = screen.getByText("Keyword Blocklist").closest(".MuiCard-root");
    fireEvent.click(
      within(card).getByRole("button", { name: "", exact: true }),
    );
    const dialog = within(await screen.findByRole("dialog"));
    const attempts = dialog.getByRole("spinbutton", {
      name: "Additional attempts",
    });
    expect(attempts).toHaveValue(2);
    fireEvent.change(attempts, { target: { value: "3" } });
    fireEvent.click(dialog.getByRole("button", { name: "Save", exact: true }));
    expect(
      onChange.mock.calls[0][0].checks["keyword-blocklist"].config,
    ).toEqual({
      ...savedReflection.config,
      reflect_max_attempts: 3,
      words: ["unsafe"],
    });
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
