import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { parseTimeoutSeconds } from "./utils";
import AddProviderDialog from "./AddProviderDialog";

const { updateMutate, fetchMutate } = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  fetchMutate: vi.fn(),
}));

vi.mock("./hooks/useGatewayConfig", () => ({
  useUpdateProvider: () => ({
    mutate: updateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useFetchProviderModels: () => ({ mutate: fetchMutate, isPending: false }),
}));

const renderEditDialog = (config) =>
  render(
    <AddProviderDialog
      open
      onClose={vi.fn()}
      gatewayId="gw-1"
      provider={{ name: "openai", config }}
    />,
  );

describe("parseTimeoutSeconds", () => {
  it("normalizes Gateway provider timeout text to integer seconds", () => {
    expect(parseTimeoutSeconds("45")).toBe(45);
    expect(parseTimeoutSeconds("45s")).toBe(45);
    expect(parseTimeoutSeconds("2m")).toBe(120);
    expect(parseTimeoutSeconds("1500ms")).toBe(2);
    expect(parseTimeoutSeconds("")).toBeNull();
    expect(parseTimeoutSeconds("soon")).toBeNull();
    expect(parseTimeoutSeconds("0s")).toBeNull();
  });
});

describe("AddProviderDialog validation", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    // The dialog fetches the provider's models when it opens in edit mode.
    fetchMutate.mockReset();
    fetchMutate.mockImplementation((_vars, opts) =>
      opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] }),
    );
  });

  it("saves an edited provider whose stored timeout came back as a number", async () => {
    // The API returns default_timeout as an integer; seeding the text field
    // with it used to throw "timeoutVal.trim is not a function" on Save.
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config.default_timeout).toBe(30);
  });

  it("summarises why a save was blocked instead of failing silently", async () => {
    renderEditDialog({ default_timeout: 30, models: [] });

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(await screen.findByText("Fix this before saving")).toBeTruthy();
    expect(
      screen.getAllByText(/Select at least one model/).length,
    ).toBeGreaterThan(0);
  });

  it("reports a malformed timeout with the offending value", async () => {
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const timeout = screen.getByLabelText("Timeout");
    await userEvent.clear(timeout);
    await userEvent.type(timeout, "soon");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(
      screen.getAllByText(/whole number of seconds.*got "soon"/).length,
    ).toBeGreaterThan(0);
  });

  it("counts multiple problems in the summary", async () => {
    renderEditDialog({ default_timeout: 30, models: [] });

    const timeout = screen.getByLabelText("Timeout");
    await userEvent.clear(timeout);
    await userEvent.type(timeout, "soon");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(await screen.findByText("Fix 2 issues before saving")).toBeTruthy();
  });
});
