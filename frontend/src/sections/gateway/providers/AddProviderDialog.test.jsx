import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { parseTimeoutSeconds } from "./utils";
import AddProviderDialog from "./AddProviderDialog";

const { updateMutate, fetchMutate, fetchState } = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  fetchMutate: vi.fn(),
  // Mutable so a test can render the dialog mid-fetch.
  fetchState: { isPending: false },
}));

vi.mock("./hooks/useGatewayConfig", () => ({
  useUpdateProvider: () => ({
    mutate: updateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useFetchProviderModels: () => ({
    mutate: fetchMutate,
    isPending: fetchState.isPending,
  }),
}));

const renderCreateDialog = () =>
  render(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />);

const renderEditDialogFor = (name, config) =>
  render(
    <AddProviderDialog
      open
      onClose={vi.fn()}
      gatewayId="gw-1"
      provider={{ name, config }}
    />,
  );

const renderEditDialog = (config) => renderEditDialogFor("openai", config);

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
    fetchState.isPending = false;
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

  it("blocks Save, and says why, when no model is selected", async () => {
    renderEditDialog({ default_timeout: 30, models: [] });

    // A model is mandatory, so the click can never succeed — the button is
    // held and the requirement is stated under the Models field instead.
    expect(screen.getByRole("button", { name: "Save Changes" }).disabled).toBe(
      true,
    );
    expect(
      screen.getAllByText(/Select at least one model to enable Save/).length,
    ).toBeGreaterThan(0);
    expect(updateMutate).not.toHaveBeenCalled();
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
    // Problems the button does not gate still reach the summary on click.
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const baseUrl = screen.getByLabelText("Base URL");
    await userEvent.clear(baseUrl);
    await userEvent.type(baseUrl, "ftp://nope");
    const timeout = screen.getByLabelText("Timeout");
    await userEvent.clear(timeout);
    await userEvent.type(timeout, "soon");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(await screen.findByText("Fix 2 issues before saving")).toBeTruthy();
  });

  it("holds Save while the provider's models are still loading", () => {
    // Saving mid-fetch validates against an empty model list and rejects a
    // valid API key, so the action stays disabled until the request settles.
    fetchState.isPending = true;
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const save = screen.getByRole("button", { name: "Loading models..." });
    expect(save.disabled).toBe(true);
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("blames the API key when the provider returns no models", async () => {
    // The gateway answers 200 with an empty list and a reason, so nothing but
    // the Models warning used to move — the key that caused it looked fine.
    fetchMutate.mockImplementation((_vars, opts) =>
      opts?.onSuccess?.({
        models: [],
        error: "Failed to fetch models from provider",
      }),
    );
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-bad");

    // Shown on the key field itself, once the debounced fetch comes back.
    expect(
      await screen.findByText(/check that it is valid for this provider/i),
    ).toBeTruthy();
    // The provider's own reason still appears under Models.
    expect(
      screen.getByText("Failed to fetch models from provider"),
    ).toBeTruthy();
    // Live feedback only — the Save-time summary stays down until Save.
    expect(screen.queryByText("Fix this before saving")).toBeNull();
    // And the rejected key holds the action, so there is nothing to click.
    expect(screen.getByRole("button", { name: "Add Provider" }).disabled).toBe(
      true,
    );
  });

  it("blocks Save when the stored key can no longer list models", async () => {
    // Exactly the screenshot case: three models already saved, so the models
    // gate passes, but the refresh failed — the key on file is the problem and
    // saving would just keep it.
    fetchMutate.mockImplementation((_vars, opts) =>
      opts?.onSuccess?.({
        models: [],
        error: "Failed to fetch models from provider",
      }),
    );
    renderEditDialog({
      default_timeout: 30,
      models: ["gpt-3.5-turbo-0125", "gpt-3.5-turbo-1106"],
    });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(true);
    expect(
      screen.getByText(/enter a new API key to save changes/i),
    ).toBeTruthy();

    // A replacement key is the way out, but it has to prove itself first.
    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-still-bad");
    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(2));

    expect(
      await screen.findByText(/check that it is valid for this provider/i),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save Changes" }).disabled).toBe(
      true,
    );
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("re-verifies a key typed in edit mode before allowing a save", async () => {
    // Edit mode used to skip the auto-fetch entirely, so a key typed here was
    // never checked — Save just wrote it.
    fetchMutate.mockImplementation((vars, opts) =>
      vars?.providerName
        ? opts?.onSuccess?.({
            models: [],
            error: "Failed to fetch models from provider",
          })
        : opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] }),
    );
    renderEditDialog({
      base_url: "https://api.openai.com/v1",
      default_timeout: 30,
      models: ["gpt-3.5-turbo-0125"],
    });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(true);

    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-good");

    // The refetch goes out with the typed credential, not the provider name.
    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(2));
    expect(fetchMutate.mock.calls[1][0]).toEqual({
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-good",
      apiFormat: "openai",
    });

    // Only once it lists models does Save open up.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save Changes" }).disabled,
      ).toBe(false),
    );

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config.api_key).toBe("sk-good");
  });
  it("keeps Bedrock editable although it lists no models", async () => {
    // Bedrock has no list endpoint and no API Key field, so gating on an empty
    // result would lock the provider out of editing with nowhere to say why.
    fetchMutate.mockImplementation((_vars, opts) =>
      opts?.onSuccess?.({ models: [] }),
    );
    renderEditDialogFor("bedrock", {
      api_format: "anthropic",
      default_timeout: 30,
      models: ["anthropic.claude-v2"],
    });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(false);
  });
});
