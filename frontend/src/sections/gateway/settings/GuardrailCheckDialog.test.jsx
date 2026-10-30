import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import GuardrailCheckDialog from "./GuardrailCheckDialog";
import GuardrailConfigTab from "./GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({
  logger: { warn: vi.fn() },
}));

// The Future AGI Eval dialog loads its Protect templates on open.
vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: { result: [{ id: "1", name: "Toxicity" }] },
    }),
  },
  endpoints: { gateway: { protectTemplates: "/protect-templates/" } },
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

describe("GuardrailConfigTab -> dialog Base URL", () => {
  const openCard = async (label, guardrails) => {
    const onChange = vi.fn();
    render(<GuardrailConfigTab guardrails={guardrails} onChange={onChange} />);
    const card = screen.getByText(label).closest(".MuiCard-root");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    await screen.findByText(`Configure: ${label}`);
    return onChange;
  };

  // An empty Base URL means "this platform": the backend fills its own
  // BASE_URL and injects the org's keys. A stored api.futureagi.com on a
  // self-hosted install is cross-environment, so no keys are injected and
  // the check fails on every request.
  it.each([
    ["a saved check", { enabled: true, config: { eval_ids: ["1"] } }],
    [
      "a saved check with an empty URL",
      { enabled: true, config: { eval_ids: ["1"], base_url: "" } },
    ],
    ["a new check", undefined],
  ])("leaves Future AGI Eval Base URL empty for %s", async (_, existing) => {
    const onChange = await openCard("Future AGI Eval", {
      checks: existing ? { "futureagi-eval": existing } : {},
    });

    expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue("");
    expect(
      screen.getByPlaceholderText("https://api.futureagi.com"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
    expect(saved.config?.base_url).toBeUndefined();
    if (existing) expect(saved.config.eval_ids).toEqual(["1"]);
  });

  it("keeps a Base URL the user typed for Future AGI Eval", async () => {
    const onChange = await openCard("Future AGI Eval", { checks: {} });

    fireEvent.change(screen.getByRole("textbox", { name: "Base URL" }), {
      target: { value: "https://dev.api.futureagi.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
    expect(saved.config.base_url).toBe("https://dev.api.futureagi.com");
  });

  it.each(["https://api.futureagi.com", "https://dev.api.futureagi.com"])(
    "preserves an explicitly saved Future AGI Eval URL: %s",
    async (baseUrl) => {
      const onChange = await openCard("Future AGI Eval", {
        checks: {
          "futureagi-eval": {
            enabled: true,
            config: { eval_ids: ["1"], base_url: baseUrl },
          },
        },
      });

      expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue(
        baseUrl,
      );
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
      expect(saved.config).toEqual({ eval_ids: ["1"], base_url: baseUrl });
    },
  );

  it("lets a user clear a saved URL to use this platform", async () => {
    const onChange = await openCard("Future AGI Eval", {
      checks: {
        "futureagi-eval": {
          enabled: true,
          config: { eval_ids: ["1"], base_url: "https://api.futureagi.com" },
        },
      },
    });

    fireEvent.change(screen.getByRole("textbox", { name: "Base URL" }), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saved = onChange.mock.calls[0][0].checks["futureagi-eval"];
    expect(saved.config).toEqual({ eval_ids: ["1"] });
  });

  it.each([
    ["a new check", undefined],
    ["a saved check", { enabled: true, config: { base_url: "" } }],
  ])(
    "still fills and saves other providers' default URL for %s",
    async (_, existing) => {
      const onChange = await openCard("CrowdStrike AIDR", {
        checks: existing ? { "crowdstrike-aidr": existing } : {},
      });

      expect(screen.getByRole("textbox", { name: "Base URL" })).toHaveValue(
        "https://api.crowdstrike.com",
      );
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      const saved = onChange.mock.calls[0][0].checks["crowdstrike-aidr"];
      expect(saved.config.base_url).toBe("https://api.crowdstrike.com");
    },
  );
});
