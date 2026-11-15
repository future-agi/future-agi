import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import PolicyDialog from "./PolicyDialog";
import GuardrailCheckDialog from "../settings/GuardrailCheckDialog";
import { GUARDRAIL_CHECKS } from "../settings/guardrailChecks";

const { create, update } = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("../keys/hooks/useApiKeys", () => ({
  useApiKeys: () => ({
    data: [
      { id: "k1", name: "Staging", keyPrefix: "sk-test", status: "active" },
    ],
  }),
}));
vi.mock("./hooks/useGuardrailPolicies", () => ({
  useCreateGuardrailPolicy: () => ({ mutateAsync: create, isPending: false }),
  useUpdateGuardrailPolicy: () => ({ mutateAsync: update, isPending: false }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const cases = [
  [
    "pii-detection",
    "PII Detection",
    { entities: [42] },
    { entities: ["email"] },
  ],
  [
    "content-moderation",
    "Content Moderation",
    { categories: [42] },
    { categories: ["toxicity"] },
  ],
  [
    "secret-detection",
    "Secret Detection",
    { types: [42] },
    { types: ["github_token"] },
  ],
  [
    "language-detection",
    "Language Detection",
    { allowed_scripts: "latin" },
    { allowed_scripts: ["latin"] },
  ],
  [
    "topic-restriction",
    "Topic Restriction",
    { forbidden_topics: [{ name: "restricted", keywords: "restricted" }] },
    { forbidden_topics: [{ name: "restricted", keywords: ["restricted"] }] },
  ],
  [
    "input-validation",
    "Input Validation",
    { deny_patterns: ["["] },
    { deny_patterns: [String.raw`\p{Greek}+`, "(?U)a+", "(?P<word>a)"] },
  ],
];
const custom = {
  name: "deployed-custom",
  enabled: true,
  action: "log",
  config: { private_option: [42] },
};
const check = (name, config, extra = {}) => ({
  name,
  enabled: true,
  action: "block",
  threshold: 0.2,
  config,
  ...extra,
});
const policy = (checks, extra = {}) => ({
  id: "p1",
  name: "Builtin correction",
  scope: "key",
  mode: "enforce",
  applied_keys: ["k1"],
  is_active: true,
  checks,
  ...extra,
});
const renderPolicy = (checks, extra = {}) => {
  const onClose = vi.fn();
  render(
    <PolicyDialog open onClose={onClose} policy={policy(checks, extra)} />,
  );
  return onClose;
};
const click = (name) =>
  fireEvent.click(screen.getByRole("button", { name, exact: true }));
const configure = (label) => click(`Configure ${label}`);
const jsonInput = (label) =>
  screen.getByRole("textbox", { name: `${label} configuration (JSON)` });

beforeEach(() => {
  create.mockReset().mockResolvedValue({ gateway_synced: true });
  update.mockReset().mockResolvedValue({ gateway_synced: true });
});

describe("selected policy builtin config R3", () => {
  it.each(cases)(
    "blocks untouched malformed %s on an unrelated policy save",
    (name, label, config) => {
      const onClose = renderPolicy([check(name, config), custom]);
      click("Save policy");
      expect(update).not.toHaveBeenCalled();
      expect(
        screen.getByText(new RegExp(`${label}:.*Configure`)),
      ).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
        "Builtin correction",
      );
      expect(onClose).not.toHaveBeenCalled();
    },
  );

  it.each(cases)(
    "corrects %s explicitly without deleting unknown keys or siblings",
    (name, label, invalid, valid) => {
      const original = { ...invalid, custom_hint: { preserve: [1, "value"] } };
      renderPolicy([check(name, original), custom]);
      configure(label);
      const input = jsonInput(label);
      expect(JSON.parse(input.value)).toEqual(original);
      click("Save");
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(update).not.toHaveBeenCalled();
      const corrected = { ...original, ...valid };
      fireEvent.change(input, { target: { value: JSON.stringify(corrected) } });
      click("Save");
      click("Save policy");
      expect(update).toHaveBeenCalledWith(
        expect.objectContaining({
          applied_keys: ["k1"],
          checks: expect.arrayContaining([
            custom,
            expect.objectContaining({
              name,
              config: corrected,
              threshold: 0.2,
            }),
          ]),
        }),
      );
    },
  );

  it.each(cases)(
    "keeps %s recoverable through disable and deactivation",
    (name, label, config) => {
      renderPolicy([check(name, config)]);
      fireEvent.click(
        screen.getByRole("checkbox", { name: label, exact: true }),
      );
      click("Save policy");
      expect(update.mock.calls[0][0].checks[0]).toMatchObject({
        enabled: false,
        config,
      });
      update.mockClear();
      fireEvent.click(
        screen.getByRole("checkbox", { name: label, exact: true }),
      );
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Active", exact: true }),
      );
      click("Save policy");
      expect(update).toHaveBeenCalledWith(
        expect.objectContaining({
          is_active: false,
          checks: [check(name, config)],
        }),
      );
    },
  );

  it.each(cases)(
    "preserves dormant %s configs but rejects reactivation",
    (name, label, config) => {
      renderPolicy([check(name, config)], { is_active: false });
      configure(label);
      expect(JSON.parse(jsonInput(label).value)).toEqual(config);
      click("Save");
      click("Save policy");
      expect(update).toHaveBeenCalledWith(
        expect.objectContaining({ is_active: false }),
      );
      update.mockClear();
      fireEvent.click(
        screen.getByRole("checkbox", { name: "Active", exact: true }),
      );
      click("Save policy");
      expect(update).not.toHaveBeenCalled();
    },
  );

  it.each(cases)(
    "makes omitted enabled/default %s config editable and retains defaults",
    (name, label) => {
      renderPolicy([check(name, undefined, { enabled: undefined })]);
      expect(
        screen.getByRole("checkbox", { name: label, exact: true }),
      ).toBeChecked();
      configure(label);
      expect(JSON.parse(jsonInput(label).value)).toEqual({});
      click("Save");
      click("Save policy");
      expect(update).toHaveBeenCalledWith(
        expect.objectContaining({
          checks: [expect.objectContaining({ name, config: {} })],
        }),
      );
    },
  );

  it.each([null, [42], false, "private-marker"])(
    "shows nonobject config %j unchanged until explicit correction",
    (config) => {
      renderPolicy([check("pii-detection", config)]);
      configure("PII Detection");
      const input = jsonInput("PII Detection");
      expect(JSON.parse(input.value)).toEqual(config);
      click("Save");
      expect(input).toHaveAttribute("aria-invalid", "true");
      expect(
        screen.getByText(/config must be a JSON object/i),
      ).not.toHaveTextContent("private-marker");
      fireEvent.change(input, { target: { value: '{"entities":["email"]}' } });
      click("Save");
      click("Save policy");
      expect(update.mock.calls[0][0].checks[0].config).toEqual({
        entities: ["email"],
      });
    },
  );

  it("does not normalize malformed PII config through the remediation shortcut", () => {
    renderPolicy([check("pii-detection", "private-marker")]);
    expect(
      screen.getByRole("combobox", { name: "Remediation" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("retains invalid JSON text and offers a value-free inline correction", () => {
    renderPolicy([check("input-validation", {})]);
    configure("Input Validation");
    const input = jsonInput("Input Validation");
    fireEvent.change(input, {
      target: { value: '{"deny_patterns": private-marker' },
    });
    click("Save");
    expect(input).toHaveValue('{"deny_patterns": private-marker');
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/Enter valid JSON/)).not.toHaveTextContent(
      "private-marker",
    );
  });

  it.each([
    ["language-detection", "Language Detection", {}],
    ["language-detection", "Language Detection", { allowed_scripts: [] }],
    ["topic-restriction", "Topic Restriction", {}],
    [
      "topic-restriction",
      "Topic Restriction",
      { allowed_topics: [], forbidden_topics: [] },
    ],
  ])(
    "labels empty %s config as no restriction in both policy and Configure",
    (name, label, config) => {
      renderPolicy([check(name, config)]);
      expect(
        screen.getByText(/No restriction configured/i),
      ).toBeInTheDocument();
      configure(label);
      expect(
        screen.getByText(/Empty settings impose no restriction/i),
      ).toBeInTheDocument();
    },
  );

  it("does not falsely label malformed language settings as no restriction", () => {
    renderPolicy([check("language-detection", { allowed_scripts: "latin" })]);
    expect(
      screen.queryByText(/No restriction configured/i),
    ).not.toBeInTheDocument();
  });

  it.each(cases)(
    "does not impose selected-policy config validation on the org %s editor",
    (name, label, config) => {
      const onSave = vi.fn();
      render(
        <GuardrailCheckDialog
          open
          onClose={vi.fn()}
          onSave={onSave}
          checkName={name}
          initialData={{ enabled: true, confidence_threshold: 50, config }}
          providerMeta={GUARDRAIL_CHECKS.find((entry) => entry.name === name)}
        />,
      );
      click("Save");
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onSave.mock.calls[0][1].confidence_threshold).toBe(50);
    },
  );
});
