import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";

import contract from "../../../../../api_contracts/gateway/guardrail-check-actions.json";
import { renderPage } from "./__tests__/helpers";
import { CATALOG, catalogEntry } from "./catalog";
import { formValuesFor, storedActionOf } from "./catalog/rule-model";
import CheckForm from "./rules/CheckForm";
import RuleDrawer from "./rules/RuleDrawer";
import GuardrailCheckDialog from "../settings/GuardrailCheckDialog";
import GuardrailConfigTab, {
  GUARDRAIL_CHECKS,
  MODEL_GUARDRAIL_CHECKS,
} from "../settings/GuardrailConfigTab";

vi.mock("notistack", async (importOriginal) => ({
  .../** @type {typeof import("notistack")} */ (await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true, disabledReason: null }),
}));
vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));

// The console offers "mask" for an organization's check exactly where the
// gateway takes it: a check whose type can rewrite the request it reads
// (pii-detection). The gateway reads the action as log on any other check.
// api_contracts/gateway/guardrail-check-actions.json says which checks take
// which actions, and the gateway's test (check_actions_contract_test.go)
// reads it too, so the console's list cannot drift from the gateway's. Two
// forms edit a check, and both offer exactly these: the Guardrails page's
// CheckForm (in the rule drawer), and the Org config editor's Guardrails tab
// (GuardrailConfigTab, which edits a check in GuardrailCheckDialog).

const expectedActions = (name) =>
  contract.mask.includes(name)
    ? ["block", "warn", "mask", "log"]
    : [...contract.actions];

// The values of the Action select's options, as the open menu lists them.
async function offeredActions() {
  const scope = screen.queryByRole("dialog") ?? document.body;
  fireEvent.mouseDown(within(scope).getByRole("combobox", { name: /Action/ }));
  const listbox = await screen.findByRole("listbox");
  return within(listbox)
    .getAllByRole("option")
    .map((o) => o.getAttribute("data-value"));
}

function renderForm(checkName, check = null) {
  const meta = catalogEntry(checkName);
  const values = formValuesFor(meta, check, (c) => storedActionOf(c));
  renderPage(
    <CheckForm
      meta={meta}
      checkName={checkName}
      values={values}
      onChange={vi.fn()}
    />,
  );
}

describe("guardrail check actions match the gateway's", () => {
  it("the console's catalog is the contract's list of checks, with their providers", () => {
    expect(CATALOG.map((c) => c.name).sort()).toEqual(
      Object.keys(contract.checks).sort(),
    );
    for (const check of CATALOG) {
      expect([
        check.name,
        "provider" in check ? check.provider : undefined,
      ]).toEqual([check.name, contract.checks[check.name].provider]);
    }
    for (const name of contract.mask) {
      expect(Object.keys(contract.checks)).toContain(name);
    }
  });

  it.each(CATALOG.map((c) => [c.name]))(
    "a new %s check is offered the gateway's actions",
    async (name) => {
      renderForm(name);
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
    "a stored %s check is offered the gateway's actions",
    async (name, checkType) => {
      renderForm(name, { name, action: "block" });
      expect(await offeredActions()).toEqual(expectedActions(checkType));
    },
  );

  it("saves mask on pii-detection, and says what it does", async () => {
    const onSave = vi.fn(() => Promise.resolve());
    renderPage(
      <RuleDrawer
        open
        meta={catalogEntry("pii-detection")}
        row={null}
        onClose={() => {}}
        onSave={onSave}
      />,
    );
    await offeredActions();
    fireEvent.click(screen.getByRole("option", { name: /^Mask/ }));
    // The menu lists each action's help too; once it closes, the one left
    // is the Action field's own helper text.
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(
      screen.getByText(
        "Replaces each match in the request with *** and lets the request through.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add check" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ action: "mask" }),
    );
  });

  // An older console offered mask on every check. Such a stored action is
  // shown, disabled, as what it is (the gateway runs it as log), and a save
  // that changes nothing keeps it, as a re-save keeps every stored setting.
  it("keeps a stored mask on a check that cannot take it, shown as log", async () => {
    const onSave = vi.fn(() => Promise.resolve());
    const stored = { enabled: true, action: "mask", confidence_threshold: 0.6 };
    renderPage(
      <RuleDrawer
        open
        meta={catalogEntry("prompt-injection")}
        row={{
          id: "prompt-injection",
          ruleName: "prompt-injection",
          label: "Prompt Injection",
          kind: "built-in",
          stage: "request",
          check: stored,
          triggers: 0,
          blocked: 0,
          accuracy: null,
        }}
        onClose={() => {}}
        onSave={onSave}
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
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "mask",
        threshold: 0.6,
        enabled: true,
      }),
    );
  });
});

describe("the Org config editor's Guardrails tab offers the gateway's actions", () => {
  const editorCatalog = [...GUARDRAIL_CHECKS, ...MODEL_GUARDRAIL_CHECKS];

  it("the editor's catalog is the contract's list of checks, with their providers", () => {
    expect(editorCatalog.map((c) => c.name).sort()).toEqual(
      Object.keys(contract.checks).sort(),
    );
    for (const check of editorCatalog) {
      expect([check.name, check.provider]).toEqual([
        check.name,
        contract.checks[check.name].provider,
      ]);
    }
  });

  // Every check's Configure dialog, whether its card is listed for a new
  // check or only for one the organization already stores.
  it.each(editorCatalog.map((c) => [c.name, c]))(
    "the Configure dialog offers %s the gateway's actions",
    async (name, meta) => {
      renderPage(
        <GuardrailCheckDialog
          open
          onClose={() => {}}
          onSave={vi.fn()}
          checkName={name}
          initialData={null}
          providerMeta={meta}
        />,
      );
      await screen.findByRole("dialog");

      expect(await offeredActions()).toEqual(expectedActions(name));
    },
  );

  it.each(GUARDRAIL_CHECKS.map((c) => [c.name, c.label]))(
    "the Guardrails tab opens %s's Configure dialog from its card",
    async (name, label) => {
      renderPage(
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

  it("saves mask on pii-detection, and says what it does", async () => {
    const onSave = vi.fn();
    renderPage(
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

  // A stored action the check cannot take is shown, disabled, as what it is
  // (the gateway runs it as log), and a save that changes nothing hands back
  // the stored check exactly.
  it("keeps a stored mask on a check that cannot take it, shown as log", async () => {
    const onSave = vi.fn();
    const stored = { enabled: true, action: "mask", confidence_threshold: 0.6 };
    renderPage(
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
