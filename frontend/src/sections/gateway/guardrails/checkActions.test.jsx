import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "src/utils/test-utils";

import contract from "../../../../../api_contracts/gateway/guardrail-check-actions.json";
import EditGuardrailDialog from "./EditGuardrailDialog";
import GuardrailCheckDialog from "../settings/GuardrailCheckDialog";
import GuardrailConfigTab, {
  GUARDRAIL_CHECKS,
  MODEL_GUARDRAIL_CHECKS,
} from "../settings/GuardrailConfigTab";

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useUpdateGuardrail: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

// The console offers "mask" for an organization's check exactly where the
// gateway takes it: a check whose type can rewrite the request it reads
// (pii-detection). The gateway reads the action as log on any other check.
// api_contracts/gateway/guardrail-check-actions.json says which checks take
// which actions, and the gateway's test (check_actions_contract_test.go)
// reads it too, so the console's list cannot drift from the gateway's.

const expectedActions = (name) =>
  contract.mask.includes(name)
    ? ["block", "warn", "mask", "log"]
    : [...contract.actions];

const catalog = [...GUARDRAIL_CHECKS, ...MODEL_GUARDRAIL_CHECKS];

// The values of the Action select's options, as the open menu lists them.
async function offeredActions() {
  fireEvent.mouseDown(
    within(screen.getByRole("dialog")).getByRole("combobox", {
      name: /Action/,
    }),
  );
  const listbox = await screen.findByRole("listbox");
  return within(listbox)
    .getAllByRole("option")
    .map((o) => o.getAttribute("data-value"));
}

describe("guardrail check actions match the gateway's", () => {
  it("the console's catalog is the contract's list of checks, with their providers", () => {
    expect(catalog.map((c) => c.name).sort()).toEqual(
      Object.keys(contract.checks).sort(),
    );
    for (const check of catalog) {
      expect([check.name, check.provider]).toEqual([
        check.name,
        contract.checks[check.name].provider,
      ]);
    }
    for (const name of contract.mask) {
      expect(Object.keys(contract.checks)).toContain(name);
    }
  });

  it.each(catalog.map((c) => [c.name, c.label]))(
    "the Rules tab offers %s the gateway's actions",
    async (name, label) => {
      render(
        <GuardrailConfigTab guardrails={{ checks: {} }} onChange={vi.fn()} />,
      );
      const card = screen
        .getAllByText(label)
        .map((n) => n.closest(".MuiCard-root"))
        .find(Boolean);
      fireEvent.click(within(card).getAllByRole("button")[0]);
      await screen.findByRole("dialog");

      expect(await offeredActions()).toEqual(expectedActions(name));
    },
  );

  it.each([
    ...Object.keys(contract.checks).map((name) => [name, name]),
    // A section stored as rules names pii-detection pii-detector.
    ["pii-detector", "pii-detection"],
    ["injection-detector", "prompt-injection"],
    ["secrets-detector", "secret-detection"],
  ])(
    "the Overview offers %s the gateway's actions",
    async (name, checkType) => {
      render(
        <EditGuardrailDialog
          open
          onClose={() => {}}
          guardrail={{ name, action: "block" }}
          gatewayId="gateway-1"
        />,
      );

      expect(await offeredActions()).toEqual(expectedActions(checkType));
    },
  );

  it("saves mask on pii-detection, and says what it does", async () => {
    const onSave = vi.fn();
    render(
      <GuardrailCheckDialog
        open
        onClose={() => {}}
        onSave={onSave}
        checkName="pii-detection"
        initialData={null}
        providerMeta={{ name: "pii-detection", label: "PII Detection" }}
      />,
    );
    await offeredActions();
    fireEvent.click(screen.getByRole("option", { name: "Mask" }));
    expect(
      screen.getByText(
        "Replaces each match in the request with *** and lets the request through.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(
      "pii-detection",
      expect.objectContaining({ action: "mask" }),
    );
  });

  // A stored mask on a check that cannot rewrite is shown, disabled, as what
  // it is (the gateway runs it as log), and a save that changes nothing keeps
  // it, as a re-save keeps every stored setting.
  it("keeps a stored mask on a check that cannot take it, shown as log", async () => {
    const onSave = vi.fn();
    const stored = { enabled: true, action: "mask", confidence_threshold: 0.6 };
    render(
      <GuardrailCheckDialog
        open
        onClose={() => {}}
        onSave={onSave}
        checkName="prompt-injection"
        initialData={stored}
        providerMeta={{ name: "prompt-injection", label: "Prompt Injection" }}
      />,
    );
    await screen.findByRole("dialog");
    expect(
      screen.getByText(
        "This check cannot take this action; the gateway runs it as Log.",
      ),
    ).toBeInTheDocument();
    const offered = await offeredActions();
    expect(offered).toEqual(["block", "warn", "log", "mask"]);
    const maskOption = screen.getByRole("option", { name: /Mask/ });
    expect(maskOption).toHaveAttribute("aria-disabled", "true");
    expect(maskOption.textContent).toBe("Mask (runs as Log)");
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith("prompt-injection", stored);
  });
});
