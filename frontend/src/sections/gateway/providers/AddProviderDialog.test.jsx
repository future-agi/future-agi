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

// Responses land on a later task, as a real request does; a synchronous mock
// closes the window the dialog's ordering bugs live in.
const deferred =
  (impl) =>
  (...args) =>
    setTimeout(() => impl(...args), 0);

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
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] }),
      ),
    );
  });

  it("saves an edited provider whose stored timeout came back as a number", async () => {
    // A numeric default_timeout used to throw "trim is not a function".
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config.default_timeout).toBe(30);
  });

  it("blocks Save, and says why, when no model is selected", async () => {
    renderEditDialog({ default_timeout: 30, models: [] });

    // The button is held, and the requirement stated under the field.
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
    // An empty 200 used to move nothing but the warning under Models.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({
          models: [],
          error: "Failed to fetch models from provider",
        }),
      ),
    );
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-bad");

    expect(
      await screen.findByText(/check that it is valid for this provider/i),
    ).toBeTruthy();
    expect(
      screen.getByText("Failed to fetch models from provider"),
    ).toBeTruthy();
    // Live feedback only — the summary stays down until Save.
    expect(screen.queryByText("Fix this before saving")).toBeNull();
    expect(screen.getByRole("button", { name: "Add Provider" }).disabled).toBe(
      true,
    );
  });

  it("blocks Save when the stored key can no longer list models", async () => {
    // Models already saved, so that gate passes — but the key on file no
    // longer works, and saving would just keep it.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({
          models: [],
          error: "Failed to fetch models from provider",
        }),
      ),
    );
    renderEditDialog({
      default_timeout: 30,
      models: ["gpt-3.5-turbo-0125", "gpt-3.5-turbo-1106"],
    });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(true);
    expect(
      screen.getByText(/Enter a new API key, or add model IDs manually/i),
    ).toBeTruthy();

    // A replacement key is the way out, but it has to prove itself.
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
    // Edit mode skipped the auto-fetch, so a key typed here went unchecked.
    fetchMutate.mockImplementation(
      deferred((vars, opts) =>
        vars?.providerName
          ? opts?.onSuccess?.({
              models: [],
              error: "Failed to fetch models from provider",
            })
          : opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] }),
      ),
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
  it("lets a hand-typed model ID unblock a provider the gateway cannot list", async () => {
    // The message says to add model IDs by hand, so that has to open Save.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: [], error: "Provider returned no models" }),
      ),
    );
    renderCreateDialog();

    // Probed with a plain {base_url}/models, and not AWS-exempt.
    await userEvent.click(screen.getByRole("combobox", { name: /Provider/ }));
    await userEvent.click(
      screen.getByRole("option", { name: "Custom / Self-hosted" }),
    );
    await userEvent.type(
      screen.getByLabelText(/Base URL/),
      "https://mine.example.com",
    );
    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-unlistable");
    await screen.findByText(/check that it is valid for this provider/i);
    expect(screen.getByRole("button", { name: "Add Provider" }).disabled).toBe(
      true,
    );

    await userEvent.type(
      screen.getByPlaceholderText(/type manually/i),
      "my-model{enter}",
    );

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add Provider" }).disabled,
      ).toBe(false),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add Provider" }));
    expect(updateMutate.mock.calls[0][0].config.models).toEqual(["my-model"]);
  });

  it("does not blame the stored key when the fetch itself fails", async () => {
    // No verdict was reached, so it must not block an unrelated edit.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onError?.(new Error("The request timed out. Please try again.")),
      ),
    );
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(false);
    expect(
      screen.queryByText(/Enter a new API key, or add model IDs manually/i),
    ).toBeNull();
    // The reason still surfaces, just not against the key.
    expect(
      screen.getByText("The request timed out. Please try again."),
    ).toBeTruthy();

    await userEvent.click(save);
    expect(updateMutate).toHaveBeenCalledTimes(1);
  });

  it("restores the stored key's verdict when a typed key is cleared", async () => {
    // The typed key's empty listing used to stay behind as the stored key's.
    fetchMutate.mockImplementation(
      deferred((vars, opts) =>
        vars?.providerName
          ? opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] })
          : opts?.onSuccess?.({
              models: [],
              error: "Failed to fetch models from provider",
            }),
      ),
    );
    renderEditDialog({
      base_url: "https://api.openai.com/v1",
      default_timeout: 30,
      models: ["gpt-4o"],
    });

    const key = screen.getByLabelText(/API Key/i);
    await userEvent.type(key, "sk-bad");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save Changes" }).disabled,
      ).toBe(true),
    );

    await userEvent.clear(key);

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save Changes" }).disabled,
      ).toBe(false),
    );
    expect(
      screen.queryByText(/Enter a new API key, or add model IDs manually/i),
    ).toBeNull();
  });

  it("keeps the field errors when the summary is dismissed", async () => {
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const timeout = screen.getByLabelText("Timeout");
    await userEvent.clear(timeout);
    await userEvent.type(timeout, "soon");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText("Fix this before saving")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /close/i }));

    // The banner goes; the marker on the field that caused it stays.
    expect(screen.queryByText("Fix this before saving")).toBeNull();
    expect(
      screen.getAllByText(/whole number of seconds.*got "soon"/).length,
    ).toBeGreaterThan(0);
  });

  it("ignores a fetch that lands after the key field was cleared", async () => {
    // The typed key's response used to arrive after the restore and overwrite
    // it, leaving a rejection message under an empty field with no way out.
    const pending = [];
    fetchMutate.mockImplementation(
      deferred((vars, opts) => {
        if (vars?.providerName) {
          opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] });
          return;
        }
        pending.push(opts);
      }),
    );
    renderEditDialog({
      base_url: "https://api.openai.com/v1",
      default_timeout: 30,
      models: ["gpt-4o"],
    });

    const key = screen.getByLabelText(/API Key/i);
    await userEvent.type(key, "sk-slow");
    await waitFor(() => expect(pending.length).toBe(1));
    await userEvent.clear(key);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save Changes" }).disabled,
      ).toBe(false),
    );

    // The abandoned request finally answers.
    pending[0].onSuccess({
      models: [],
      error: "Failed to fetch models from provider",
    });

    expect(screen.getByRole("button", { name: "Save Changes" }).disabled).toBe(
      false,
    );
    expect(
      screen.queryByText(/check that it is valid for this provider/i),
    ).toBeNull();
  });

  it("re-arms the gate when the hand-entered model is removed", async () => {
    // The override has to follow the chips: a scratch ID typed and then deleted
    // must not leave the block lifted for a key the provider went on to reject.
    let reject = false;
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.(
          reject
            ? { models: [], error: "Failed to fetch models from provider" }
            : { models: ["gpt-4o", "gpt-4o-mini"] },
        ),
      ),
    );
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-good");
    const input = await screen.findByPlaceholderText(/Select models/i);

    await userEvent.type(input, "scratch-id{enter}");
    await userEvent.type(input, "{backspace}");
    await userEvent.type(input, "gpt-4o{enter}");
    expect(screen.queryByText("scratch-id")).toBeNull();
    expect(screen.getAllByText("gpt-4o").length).toBeGreaterThan(0);

    reject = true;
    await userEvent.clear(screen.getByLabelText(/API Key/i));
    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-rejected");

    // Only a listed model is selected now, so the rejected key still blocks.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add Provider" }).disabled,
      ).toBe(true),
    );
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("takes pasted model IDs as a manual list", async () => {
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: [], error: "Provider returned no models" }),
      ),
    );
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-unlistable");
    await screen.findByText(/check that it is valid for this provider/i);

    const input = screen.getByPlaceholderText(/type manually/i);
    await userEvent.click(input);
    await userEvent.paste("model-a, model-b");

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add Provider" }).disabled,
      ).toBe(false),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add Provider" }));
    expect(updateMutate.mock.calls[0][0].config.models).toEqual([
      "model-a",
      "model-b",
    ]);
  });

  it("keeps the stored listing when the dialog opens in edit mode", async () => {
    // The by-name fetch is in flight while the mount effects settle, and the
    // blank key field must not cancel it.
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    expect(await screen.findByText("(2 available)")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate).toHaveBeenCalledTimes(1);
  });

  it("keeps Bedrock editable although it lists no models", async () => {
    // No list endpoint and no API Key field to explain a block on.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) => opts?.onSuccess?.({ models: [] })),
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
