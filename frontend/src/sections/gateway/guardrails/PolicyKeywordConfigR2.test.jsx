import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import PolicyDialog from "./PolicyDialog";
import GuardrailCheckDialog from "../settings/GuardrailCheckDialog";
import { GUARDRAIL_CHECKS } from "../settings/guardrailChecks";

const { create, update, snackbar } = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  snackbar: vi.fn(),
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
vi.mock("notistack", () => ({ enqueueSnackbar: snackbar }));

const keyword = (config, extra = {}) => ({
  name: "keyword-blocklist",
  enabled: true,
  action: "block",
  threshold: 0.2,
  config,
  ...extra,
});
const savedPolicy = (checks, extra = {}) => ({
  id: "p1",
  name: "Keyword rollout",
  scope: "key",
  mode: "enforce",
  applied_keys: ["k1"],
  checks,
  is_active: true,
  ...extra,
});
const renderPolicy = (checks, extra = {}) => {
  const onClose = vi.fn();
  render(
    <PolicyDialog open onClose={onClose} policy={savedPolicy(checks, extra)} />,
  );
  return onClose;
};
const savePolicy = () =>
  userEvent.click(screen.getByRole("button", { name: "Save policy" }));

beforeEach(() => {
  create.mockReset().mockResolvedValue({ gateway_synced: true });
  update.mockReset().mockResolvedValue({ gateway_synced: true });
  snackbar.mockReset();
});

describe("selected policy keyword configuration R2", () => {
  it("prevents creating an enabled empty check, then accepts words through Configure", async () => {
    const onClose = vi.fn();
    render(<PolicyDialog open onClose={onClose} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
      target: { value: "New blocklist" },
    });
    await userEvent.click(screen.getByRole("combobox", { name: "API keys" }));
    await userEvent.click(screen.getByText(/Staging \(sk-test/));
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Keyword Blocklist" }),
    );
    await savePolicy();
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByText(/Keyword Blocklist:.*words/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.change(
      screen.getByRole("textbox", { name: "Blocked Keywords" }),
      { target: { value: "restricted" } },
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    await savePolicy();
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        applied_keys: ["k1"],
        checks: [
          expect.objectContaining({ config: { words: ["restricted"] } }),
        ],
      }),
    );
  });

  it.each([
    ["missing config", undefined],
    ["null config", null],
    ["missing words", {}],
    ["string words", { words: "restricted" }],
    ["null words", { words: null }],
    ["empty words", { words: [] }],
    ["blank entry", { words: ["restricted", ""] }],
    ["whitespace entry", { words: [" \t\n"] }],
    ["mixed entries", { words: ["restricted", 7, null] }],
    ["object entry", { words: [{}] }],
    ["nonboolean whole word", { words: ["restricted"], whole_word: "false" }],
    ["null whole word", { words: ["restricted"], whole_word: null }],
    ["zero max matches", { words: ["restricted"], max_matches: 0 }],
    ["fraction max matches", { words: ["restricted"], max_matches: 1.5 }],
    ["string max matches", { words: ["restricted"], max_matches: "2" }],
    ["overflow max matches", { words: ["restricted"], max_matches: 2 ** 63 }],
  ])("rejects active enabled %s without dropping edits", async (_, config) => {
    const onClose = renderPolicy([keyword(config)]);
    await savePolicy();
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByText(/Keyword Blocklist:/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue(
      "Keyword rollout",
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("offers correction for legacy checks whose omitted enabled flag defaults true", async () => {
    renderPolicy([keyword({ words: "restricted" }, { enabled: undefined })]);
    expect(
      screen.getByRole("checkbox", { name: "Keyword Blocklist" }),
    ).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(
      screen.getByRole("textbox", { name: "Keyword configuration (JSON)" }),
    ).toBeInTheDocument();
  });

  it("keeps malformed legacy JSON visible, rejects untouched mixed arrays, and corrects atomically", async () => {
    const malformed = {
      words: ["restricted", 7],
      whole_word: true,
      max_matches: 2,
    };
    const custom = {
      name: "deployed-custom",
      enabled: true,
      action: "log",
      config: { arbitrary: [1, "value"] },
    };
    renderPolicy([keyword(malformed), custom]);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    const input = screen.getByRole("textbox", {
      name: "Keyword configuration (JSON)",
    });
    expect(JSON.parse(input.value)).toEqual(malformed);
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    expect(
      screen.getByRole("textbox", { name: "Keyword configuration (JSON)" }),
    ).toBeInTheDocument();
    const corrected = {
      ...malformed,
      words: ["restricted", " spaced phrase ", "foo*", "a.b", "hello, world"],
    };
    fireEvent.change(input, { target: { value: JSON.stringify(corrected) } });
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    await savePolicy();
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({
        scope: "key",
        applied_keys: ["k1"],
        checks: expect.arrayContaining([
          custom,
          expect.objectContaining({ config: corrected, threshold: 0.2 }),
        ]),
      }),
    );
  });

  it("preserves spaces, commas, case, wildcards, literals, duplicates and optional scoring settings", async () => {
    const config = {
      words: [" MiXeD phrase ", "hello, world", "foo*", "a.b", "a.b"],
      whole_word: false,
      max_matches: 3,
      custom_hint: "keep",
    };
    renderPolicy([
      keyword(config, { threshold: undefined, confidence_threshold: 0.35 }),
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(
      screen.getByRole("textbox", { name: "Blocked Keywords" }),
    ).toHaveValue(config.words.join("\n"));
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    await savePolicy();
    expect(update.mock.calls[0][0].checks[0]).toMatchObject({
      config,
      threshold: 0.35,
    });
    expect(
      JSON.parse(JSON.stringify(update.mock.calls[0][0].checks[0])),
    ).not.toHaveProperty("confidence_threshold");
  });

  it("shows inline words errors in Configure instead of silently stripping blank entries", async () => {
    renderPolicy([keyword({ words: ["restricted"] })]);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.change(
      screen.getByRole("textbox", { name: "Blocked Keywords" }),
      { target: { value: "restricted\n \nother" } },
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    expect(
      screen.getByRole("textbox", { name: "Blocked Keywords" }),
    ).toHaveAttribute("aria-invalid", "true");
    expect(update).not.toHaveBeenCalled();
  });

  it("allows disabling a malformed check without changing its config", async () => {
    const config = { words: "restricted" };
    renderPolicy([keyword(config)]);
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Keyword Blocklist" }),
    );
    await savePolicy();
    expect(update.mock.calls[0][0].checks[0]).toMatchObject({
      enabled: false,
      config,
    });
  });

  it("allows deactivation with malformed checks resubmitted unchanged", async () => {
    const check = keyword({ words: "restricted" });
    renderPolicy([check]);
    await userEvent.click(screen.getByRole("checkbox", { name: "Active" }));
    await savePolicy();
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ is_active: false, checks: [check] }),
    );
  });

  it("does not activate a dormant row on an unrelated save, but validates reactivation", async () => {
    renderPolicy([keyword({ words: [] })], { is_active: false });
    await savePolicy();
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({ is_active: false }),
    );
    update.mockClear();
    await userEvent.click(screen.getByRole("checkbox", { name: "Active" }));
    await savePolicy();
    expect(update).not.toHaveBeenCalled();
  });

  it("keeps server errors inline and edits intact; pending sync is still a warning", async () => {
    update.mockRejectedValueOnce({
      details: {
        checks: [
          { config: { words: ["Server rejected keyword configuration"] } },
        ],
      },
    });
    update.mockResolvedValueOnce({
      gateway_synced: false,
      gateway_warning: "Sync pending",
    });
    const onClose = renderPolicy([keyword({ words: ["restricted"] })]);
    await savePolicy();
    expect(
      await screen.findByText("Server rejected keyword configuration"),
    ).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await savePolicy();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[1][0].checks[0].config).toEqual({
      words: ["restricted"],
    });
    expect(snackbar).toHaveBeenCalledWith("Sync pending", {
      variant: "warning",
    });
  });

  it("retains embedded newlines in valid words through the lossless JSON editor", async () => {
    const config = {
      words: ["two\nlines", " spaced "],
      whole_word: true,
      max_matches: 2,
    };
    renderPolicy([keyword(config)]);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(
      JSON.parse(
        screen.getByRole("textbox", { name: "Keyword configuration (JSON)" })
          .value,
      ),
    ).toEqual(config);
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    await savePolicy();
    expect(update.mock.calls[0][0].checks[0].config).toEqual(config);
  });

  it("keeps invalid JSON edits open with inline guidance", async () => {
    renderPolicy([keyword({ words: "restricted" })]);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    const input = screen.getByRole("textbox", {
      name: "Keyword configuration (JSON)",
    });
    fireEvent.change(input, { target: { value: '{"words":' } });
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    expect(input).toHaveValue('{"words":');
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(update).not.toHaveBeenCalled();
  });

  it("lets users edit whole-word and positive integer scoring options", async () => {
    renderPolicy([keyword({ words: ["restricted"] })]);
    await userEvent.click(screen.getByRole("button", { name: "Configure" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Whole word" }));
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max matches" }), {
      target: { value: "1.5" },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    expect(
      screen.getByRole("spinbutton", { name: "Max matches" }),
    ).toHaveAttribute("aria-invalid", "true");
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max matches" }), {
      target: { value: "2" },
    });
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    await savePolicy();
    expect(update.mock.calls[0][0].checks[0].config).toEqual({
      words: ["restricted"],
      whole_word: true,
      max_matches: 2,
    });
  });

  it("does not tighten the shared org editor without policy opt-in", async () => {
    const onSave = vi.fn();
    render(
      <GuardrailCheckDialog
        open
        onClose={vi.fn()}
        onSave={onSave}
        checkName="keyword-blocklist"
        initialData={{ enabled: true, config: { words: [] } }}
        providerMeta={GUARDRAIL_CHECKS.find(
          (check) => check.name === "keyword-blocklist",
        )}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Save", exact: true }),
    );
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});
