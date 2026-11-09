import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import { renderPage } from "./helpers";
import AddCheckDialog from "../rules/AddCheckDialog";
import { catalogEntry, partnerConfigured } from "../catalog";
import GuardrailConfigTab from "../../settings/GuardrailConfigTab";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

const PARTNERS = [
  "Future AGI Eval",
  "Llama Guard",
  "Azure Content Safety",
  "Presidio PII",
  "Lakera Guard",
  "AWS Bedrock Guardrails",
  "HiddenLayer",
];

const lakeraStored = {
  enabled: true,
  action: "block",
  provider: "lakera",
  config: { api_key: "__encrypted__" },
};

describe("Partner checks are shown only once their credential is configured", () => {
  it("Add a check offers no partner service and no partner group", () => {
    renderPage(
      <AddCheckDialog
        open
        configured={new Set()}
        onClose={() => {}}
        onPick={() => {}}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Add a check" });
    expect(
      within(dialog).queryByText("Future AGI and partner services"),
    ).toBeNull();
    for (const label of PARTNERS) {
      expect(within(dialog).queryByText(label)).toBeNull();
    }
    expect(within(dialog).getByText("PII Detection")).toBeInTheDocument();
  });

  it("the Org config Guardrails tab shows no partner check and no AI-Powered Checks group while none is configured", () => {
    renderPage(
      <GuardrailConfigTab guardrails={{ checks: {} }} onChange={() => {}} />,
    );
    expect(screen.queryByText("AI-Powered Checks")).toBeNull();
    for (const label of PARTNERS) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(screen.getByText("PII Detection")).toBeInTheDocument();
  });

  it("the Org config Guardrails tab shows a partner check stored with its credential", () => {
    renderPage(
      <GuardrailConfigTab
        guardrails={{ checks: { "lakera-guard": lakeraStored } }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("AI-Powered Checks")).toBeInTheDocument();
    expect(screen.getByText("Lakera Guard")).toBeInTheDocument();
    expect(screen.queryByText("Llama Guard")).toBeNull();
  });

  it("counts a check as configured only with its credential stored", () => {
    const lakera = catalogEntry("lakera-guard");
    expect(partnerConfigured(lakera, lakeraStored)).toBe(true);
    expect(partnerConfigured(lakera, null)).toBe(false);
    expect(
      partnerConfigured(lakera, { enabled: true, provider: "lakera" }),
    ).toBe(false);
    const presidio = catalogEntry("presidio-pii");
    expect(partnerConfigured(presidio, { enabled: true })).toBe(false);
    expect(
      partnerConfigured(presidio, {
        config: { endpoint: "http://presidio:5001" },
      }),
    ).toBe(true);
  });
});
