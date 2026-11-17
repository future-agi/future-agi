// R-LIT: preserve inherited credential fixtures literally; no encoding or new runtime construction.
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Exercise discovery-ON behavior; launch-OFF has a separate unmocked flag suite.
vi.mock("./providerDiscovery", () => ({ PROVIDER_MODEL_DISCOVERY_ENABLED: true }));
import {
  act,
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  DEFAULT_API_PATH_PREFIX,
  apiFormatLabel,
  getApiPathPrefix,
  isSystemOneFormat,
  normalizeApiFormat,
  parseTimeoutSeconds,
  withApiPathPrefix,
} from "./utils";
import {
  DECISIONS_ONLY_TAG,
  RESPONSES_ONLY_TAG,
  SYSTEM_ONE_MODELS_HELP,
} from "./modelCompatibility";
import AddProviderDialog from "./AddProviderDialog";

const {
  updateMutate,
  fetchMutate,
  fetchState,
  refreshState,
  snackbars,
  savedProjection,
} = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  fetchMutate: vi.fn(),
  // Mutable so a test can render the dialog mid-fetch.
  fetchState: { isPending: false },
  // Whether the post-save config re-read failed.
  refreshState: { failed: false },
  snackbars: [],
  savedProjection: { models: [] },
}));

vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => refreshState.failed,
  useUpdateProvider: () => ({
    mutate: updateMutate,
    isPending: false,
    isError: false,
    error: null,
  }),
  useFetchProviderModels: () => ({
    // This inherited suite mocks the no-probe hydration separately from
    // discovery. The new HTTP suite exercises both real hook requests.
    mutate: (vars, opts) =>
      vars.savedModelsOnly
        ? opts?.onSuccess?.({ models: savedProjection.models })
        : fetchMutate(vars, opts),
    isPending: fetchState.isPending,
  }),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => snackbars.push({ message, options }),
}));

// Responses land on a later task, as a real request does; a synchronous mock
// closes the window the dialog's ordering bugs live in.
// Long enough for the dialog's 600 ms fetch debounce plus a loaded host.
const DEBOUNCED_WAIT = 4000;

const deferred =
  (impl) =>
  (...args) =>
    setTimeout(() => impl(...args), 0);

// The dialog reads the query cache to tell a clean save from one whose
// follow-up re-read failed, so it needs a client in scope.
const withClient = (ui) => (
  <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
);

const renderCreateDialog = () =>
  render(
    withClient(<AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" />),
  );

const renderEditDialogFor = (name, config) => {
  savedProjection.models = config.models || [];
  return render(
    withClient(
      <AddProviderDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        provider={{ name, config }}
      />,
    ),
  );
};

const renderEditDialog = (config) => renderEditDialogFor("openai", config);

// Existing connection-edit scenarios now open the disclosure before interacting.
const connectionField = (label) => {
  const toggle = screen.getByRole("button", { name: "Advanced connection settings" });
  if (toggle.getAttribute("aria-expanded") === "false") fireEvent.click(toggle);
  return screen.getByLabelText(label);
};

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
    refreshState.failed = false;
    snackbars.length = 0;
    // The dialog fetches the provider's models when it opens in edit mode.
    fetchMutate.mockReset();
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] }),
      ),
    );
  });

  it("offers Vertex with pasted service-account JSON, without an API key or model fetch", async () => {
    renderCreateDialog();
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(
      screen.getByRole("option", { name: "Google Vertex AI" }),
    );

    fireEvent.change(screen.getByLabelText(/^Google Cloud project ID/), {
      target: { value: "demo-project" },
    });
    fireEvent.change(screen.getByLabelText(/^Service-account JSON/), {
      target: {
        // R-LIT: serialize visibly synthetic service-account fixture fields only.
        value: JSON.stringify({
          type: "service_account",
          project_id: "demo-project",
          client_email: "demo@example.iam.gserviceaccount.com",
          private_key: "test-key",
          token_uri: "https://oauth2.googleapis.com/token",
        }),
      },
    });
    const modelInput = screen.getByPlaceholderText(/model IDs/i);
    await userEvent.type(modelInput, "vertex_ai/gemini-3.7-flash{enter}");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    expect(fetchMutate).not.toHaveBeenCalled();
    expect(updateMutate).toHaveBeenCalledTimes(1);
    const payload = updateMutate.mock.calls[0][0];
    expect(payload.name).toBe("vertex");
    expect(payload.config.api_format).toBe("gemini");
    expect(payload.config.gcp_project).toBe("demo-project");
    expect(payload.config.gcp_location).toBe("us-central1");
    expect(payload.config.models).toContain("vertex_ai/gemini-3.7-flash");
    expect(payload.config.service_account_json).toContain(
      '"type":"service_account"',
    );
    expect(payload.config).not.toHaveProperty("api_key");
  });

  it("saves an edited provider whose stored timeout came back as a number", async () => {
    // A numeric default_timeout used to throw "trim is not a function".
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config.default_timeout).toBe(30);
  });

  const saveEditedProvider = async () => {
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    const [, callbacks] = updateMutate.mock.calls[0];
    await act(async () => callbacks.onSuccess?.({}));
  };

  it("reports a clean save as a success", async () => {
    refreshState.failed = false;

    await saveEditedProvider();

    expect(snackbars).toEqual([
      { message: 'Provider "openai" updated', options: { variant: "success" } },
    ]);
  });

  it("names the half that failed when only the config re-read failed", async () => {
    // The POST landed; the follow-up read did not. Reporting this as a failed
    // save is what made users distrust the screen.
    refreshState.failed = true;

    await saveEditedProvider();

    expect(snackbars).toHaveLength(1);
    expect(snackbars[0].options.variant).toBe("warning");
    expect(snackbars[0].message).toBe(
      'Provider "openai" updated, but the provider list could not be reloaded — refresh the page to see it.',
    );
  });

  it("shows the server's reason when the save itself fails", async () => {
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    const [, callbacks] = updateMutate.mock.calls[0];

    await act(async () =>
      callbacks.onError?.({ statusCode: 400, result: "Unknown provider name" }),
    );

    expect(snackbars).toEqual([
      { message: "Unknown provider name", options: { variant: "error" } },
    ]);
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

  it("reports a malformed timeout with the offending value and focuses it after expanding", async () => {
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const timeout = connectionField("Timeout");
    await userEvent.clear(timeout);
    await userEvent.type(timeout, "soon");
    const toggle = screen.getByRole("button", { name: "Advanced connection settings" });
    await userEvent.click(toggle);
    expect(timeout).not.toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(timeout).toBeVisible();
    expect(timeout).toHaveFocus();
    expect(
      screen.getAllByText(/whole number of seconds.*got "soon"/).length,
    ).toBeGreaterThan(0);
  });

  it("does not repeat discovery or save when Advanced is toggled on an existing connection", () => {
    vi.useFakeTimers();
    try {
      renderEditDialog({ default_timeout: 45, models: ["gpt-4o"] });
      act(() => vi.advanceTimersByTime(1000));
      expect(fetchMutate).toHaveBeenCalledTimes(1);
      const toggle = screen.getByRole("button", { name: "Advanced connection settings" });
      expect(toggle).toHaveAttribute("aria-expanded", "false");
      fireEvent.click(toggle);
      fireEvent.click(toggle);
      act(() => vi.advanceTimersByTime(1000));
      expect(fetchMutate).toHaveBeenCalledTimes(1);
      expect(updateMutate).not.toHaveBeenCalled();
      expect(screen.getByLabelText("Timeout")).not.toBeVisible();
      expect(screen.getByText(/Timeout: 45s/)).toBeVisible();
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
    }
  });

  it("counts multiple problems in the summary", async () => {
    // Problems the button does not gate still reach the summary on click.
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const baseUrl = connectionField("Base URL");
    await userEvent.clear(baseUrl);
    await userEvent.type(baseUrl, "ftp://nope");
    const timeout = connectionField("Timeout");
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
    expect(screen.getByRole("button", { name: "Add provider" }).disabled).toBe(
      true,
    );
  });

  it("shows a redirect refusal beside model discovery without blaming a typed key", async () => {
    const message =
      "Models could not be loaded because the provider returned an HTTP 3xx response, " +
      "and redirects are not followed. Check the Base URL and API Path Prefix.";
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onError?.(
          Object.assign(new Error(message), {
            statusCode: 400,
            code: "PROVIDER_REDIRECT_REFUSED",
          }),
        ),
      ),
    );
    renderCreateDialog();
    const key = screen.getByLabelText(/API Key/i);
    fireEvent.change(key, { target: { value: "sk-redirect-test" } });

    const warning = await screen.findByText(
      message,
      {},
      {
        timeout: DEBOUNCED_WAIT,
      },
    );
    expect(warning.closest(".MuiAlert-root")).toHaveClass(
      "MuiAlert-outlinedWarning",
    );
    expect(key).toHaveValue("sk-redirect-test");
    expect(key).toHaveAttribute("aria-invalid", "false");
    expect(
      screen.queryByText(/check that it is valid for this provider/i),
    ).toBeNull();
    expect(screen.queryByText(/\(\d+ available\)/)).toBeNull();
    expect(fetchMutate).toHaveBeenCalledTimes(1);
    expect(fetchMutate.mock.calls[0][0].apiKey).toBe("sk-redirect-test");
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("does not mark a typed-key redirect as a completed listing during an edit", async () => {
    const message =
      "Models could not be loaded because the provider returned an HTTP 3xx response, " +
      "and redirects are not followed. Check the Base URL and API Path Prefix.";
    fetchMutate.mockImplementation(
      deferred((vars, opts) =>
        vars?.providerName && !vars?.apiKey
          ? opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] })
          : opts?.onError?.(
              Object.assign(new Error(message), {
                statusCode: 400,
                code: "PROVIDER_REDIRECT_REFUSED",
              }),
            ),
      ),
    );
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });
    await screen.findByText("(2 available)");
    const key = screen.getByLabelText(/API Key/i);
    fireEvent.change(key, { target: { value: "sk-redirect-test" } });

    const warning = await screen.findByText(
      message,
      {},
      {
        timeout: DEBOUNCED_WAIT,
      },
    );
    expect(warning.closest(".MuiAlert-root")).toHaveClass(
      "MuiAlert-outlinedWarning",
    );
    expect(key).toHaveValue("sk-redirect-test");
    expect(key).toHaveAttribute("aria-invalid", "false");
    expect(
      screen.queryByText(/check that it is valid for this provider/i),
    ).toBeNull();
    expect(screen.queryByText(/\(\d+ available\)/)).toBeNull();
    expect(fetchMutate).toHaveBeenCalledTimes(2);
    expect(fetchMutate.mock.calls[1][0].apiKey).toBe("sk-redirect-test");

    // The stored model was offered, not manually entered. If hasFetched were
    // true with this empty listing, Save-time validation would blame the key.
    const save = screen.getByRole("button", { name: "Save Changes" });
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config).toMatchObject({
      api_key: "sk-redirect-test",
      models: ["gpt-4o"],
    });
    expect(key).toHaveAttribute("aria-invalid", "false");
    expect(
      screen.queryByText(/check that it is valid for this provider/i),
    ).toBeNull();
  });

  it("still blames a typed key for a provider_authentication_failed verdict", async () => {
    const message =
      "Provider rejected the credential. Check its permissions or rotate it, then retry.";
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({
          models: [],
          error: message,
          code: "provider_authentication_failed",
        }),
      ),
    );
    renderCreateDialog();
    const key = screen.getByLabelText(/API Key/i);
    fireEvent.change(key, { target: { value: "sk-rejected-test" } });

    const genericRefusal =
      "Couldn't load any models with this key. Check that it is valid for this " +
      "provider, or add model IDs manually below.";
    expect(
      (await screen.findAllByText(genericRefusal, {}, { timeout: DEBOUNCED_WAIT }))
        .length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(message)).toBeNull();
    expect(key).toHaveValue("sk-rejected-test");
    expect(key).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Add provider" })).toBeDisabled();
    expect(fetchMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate).not.toHaveBeenCalled();
  });

  // A provider whose model list fails (its upstream is down, or the list
  // answers nothing) can still have its timeout or base URL edited: Save
  // keeps the models it already has, and the failure reads as a warning.
  it("saves with the stored models when the provider's model list fails, warning, not blaming the key", async () => {
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({
          models: [],
          error: "Failed to fetch models from provider",
        }),
      ),
    );
    renderEditDialogFor("walk-flaky", {
      base_url: "http://flaky.example.test/v1",
      default_timeout: 30,
      models: ["walk-flaky-model"],
    });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(1));
    const warning = await screen.findByText(
      /Failed to fetch models from provider/,
    );
    expect(warning.closest(".MuiAlert-root")).toHaveClass(
      "MuiAlert-outlinedWarning",
    );
    // Two sentences: what failed, then what Save does.
    expect(warning.closest(".MuiAlert-root")).toHaveTextContent(
      /^Failed to fetch models from provider\. Save keeps this provider's current models\.$/,
    );
    expect(save).toBeEnabled();
    // The key field is not in an error state: blank keeps the stored key.
    const key = screen.getByLabelText(/API Key/i);
    expect(key).toHaveAttribute(
      "placeholder",
      "Leave blank to keep current key",
    );
    expect(key).toHaveAttribute("aria-invalid", "false");
    expect(
      screen.queryByText(/Enter a new API key, or add model IDs manually/i),
    ).toBeNull();

    fireEvent.change(connectionField(/Timeout/), {
      target: { value: "45" },
    });
    await userEvent.click(save);
    expect(updateMutate).toHaveBeenCalledTimes(1);
    const { config } = updateMutate.mock.calls[0][0];
    expect(config.models).toEqual(["walk-flaky-model"]);
    expect(config).not.toHaveProperty("api_key");
  });

  it("drops the Base URL Required hint once the field is filled", async () => {
    renderEditDialogFor("walk-flaky", {
      base_url: "http://flaky.example.test/v1",
      default_timeout: 30,
      models: ["walk-flaky-model"],
    });
    const url = await screen.findByLabelText(/Base URL/);
    expect(screen.queryByText(/Required — enter your endpoint URL/)).toBeNull();
    fireEvent.change(url, { target: { value: "" } });
    expect(
      screen.getByText(/Required — enter your endpoint URL/),
    ).toBeInTheDocument();
  });

  it("blocks Save when a newly typed key lists no models", async () => {
    fetchMutate.mockImplementation(
      deferred((vars, opts) =>
        vars?.providerName && !vars?.apiKey
          ? opts?.onSuccess?.({ models: ["gpt-4o"] })
          : opts?.onSuccess?.({
              models: [],
              error: "Failed to fetch models from provider",
            }),
      ),
    );
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });
    await screen.findByRole("button", { name: "Save Changes" });
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
        vars?.providerName && !vars?.apiKey
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

    await screen.findByRole("button", { name: "Save Changes" });

    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-good");

    // The refetch carries the replacement AND saved-provider context.
    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(2));
    expect(fetchMutate.mock.calls[1][0]).toEqual({
      providerName: "openai",
      baseUrl: "https://api.openai.com/v1",
      apiKey: "sk-good",
      apiFormat: "openai",
      apiPathPrefix: "/v1",
    });

    // Once it lists models, Save is open.
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
      connectionField(/Base URL/),
      "https://mine.example.com",
    );
    await userEvent.type(screen.getByLabelText(/API Key/i), "sk-unlistable");
    await screen.findByText(/check that it is valid for this provider/i);
    expect(screen.getByRole("button", { name: "Add provider" }).disabled).toBe(
      true,
    );

    await userEvent.type(
      screen.getByPlaceholderText(/type manually/i),
      "my-model{enter}",
    );

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add provider" }).disabled,
      ).toBe(false),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
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
      screen.getByText(/The request timed out\. Please try again\./),
    ).toBeTruthy();

    await userEvent.click(save);
    expect(updateMutate).toHaveBeenCalledTimes(1);
  });

  it("restores the stored key's verdict when a typed key is cleared", async () => {
    // The typed key's empty listing used to stay behind as the stored key's.
    fetchMutate.mockImplementation(
      deferred((vars, opts) =>
        vars?.providerName && !vars?.apiKey
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
    // The dialog debounces the fetch by 600 ms; the default 1 s wait left
    // 400 ms for the render and flaked under shared-host load.
    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: "Save Changes" }).disabled,
        ).toBe(true),
      { timeout: DEBOUNCED_WAIT },
    );

    await userEvent.clear(key);

    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: "Save Changes" }).disabled,
        ).toBe(false),
      { timeout: DEBOUNCED_WAIT },
    );
    expect(
      screen.queryByText(/Enter a new API key, or add model IDs manually/i),
    ).toBeNull();
  });

  it("keeps the field errors when the summary is dismissed", async () => {
    renderEditDialog({ default_timeout: 30, models: ["gpt-4o"] });

    const timeout = connectionField("Timeout");
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
        if (vars?.providerName && !vars?.apiKey) {
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
        screen.getByRole("button", { name: "Add provider" }).disabled,
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
        screen.getByRole("button", { name: "Add provider" }).disabled,
      ).toBe(false),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
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
      base_url: "https://bedrock-runtime.us-east-1.amazonaws.com",
      api_format: "anthropic",
      default_timeout: 30,
      models: ["anthropic.claude-v2"],
    });

    const save = await screen.findByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(false);
  });
});

describe("AddProviderDialog Bedrock region preservation", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchMutate.mockReset();
    fetchState.isPending = false;
    refreshState.failed = false;
  });

  it.each(["us-west-2", "eu-west-1"])("hydrates %s and preserves the endpoint on a model/timeout-only edit", async (region) => {
    const baseUrl = `https://bedrock-runtime.${region}.amazonaws.com`;
    renderEditDialogFor("bedrock", {
      base_url: baseUrl, api_format: "bedrock", models: ["saved-model"], default_timeout: 30,
    });
    expect(screen.getByLabelText(/^AWS Region/)).toHaveTextContent(region);
    fireEvent.change(connectionField("Timeout"), { target: { value: "45" } });
    await userEvent.type(screen.getByPlaceholderText(/model IDs/i), "added-model{enter}");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate).toHaveBeenCalledOnce();
    expect(updateMutate.mock.calls[0][0].config).toMatchObject({
      aws_region: region, base_url: baseUrl, default_timeout: 45,
      models: ["saved-model", "added-model"],
    });
  });

  it("uses saved region metadata for a nonstandard endpoint without rewriting that endpoint", async () => {
    renderEditDialogFor("bedrock", {
      aws_region: "ap-southeast-2", base_url: "https://bedrock-proxy.example.test",
      api_format: "bedrock", models: ["saved-model"],
    });
    expect(screen.getByLabelText(/^AWS Region/)).toHaveTextContent("ap-southeast-2");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config).toMatchObject({
      aws_region: "ap-southeast-2", base_url: "https://bedrock-proxy.example.test",
    });
  });

  it("updates both the region and endpoint only after an explicit region change", async () => {
    renderEditDialogFor("bedrock", {
      base_url: "https://bedrock-runtime.us-west-2.amazonaws.com",
      api_format: "bedrock", models: ["saved-model"],
    });
    await userEvent.click(screen.getByLabelText(/^AWS Region/));
    await userEvent.click(screen.getByRole("option", { name: /eu-west-1/ }));
    expect(updateMutate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config).toMatchObject({
      aws_region: "eu-west-1", base_url: "https://bedrock-runtime.eu-west-1.amazonaws.com",
    });
  });

  it.each([undefined, "https://bedrock-proxy.example.test", "https://bedrock-runtime.us-west-2.amazonaws.com.example.test"])(
    "requires explicit region selection for an unknown saved endpoint %j", async (baseUrl) => {
      renderEditDialogFor("bedrock", { base_url: baseUrl, api_format: "bedrock", models: ["saved-model"] });
      expect(screen.getByText(/Saved region could not be determined/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
      expect(updateMutate).not.toHaveBeenCalled();
      await userEvent.click(screen.getByLabelText(/^AWS Region/));
      await userEvent.click(screen.getByRole("option", { name: /us-east-1/ }));
      await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
      expect(updateMutate.mock.calls[0][0].config).toMatchObject({
        aws_region: "us-east-1", base_url: "https://bedrock-runtime.us-east-1.amazonaws.com",
      });
    },
  );

  it("resets region, AWS credentials and model drafts when switching saved providers and reopening Add", async () => {
    savedProjection.models = ["saved-model"];
    const props = { onClose: vi.fn(), gatewayId: "gw-1" };
    const first = { name: "bedrock", config: { base_url: "https://bedrock-runtime.us-west-2.amazonaws.com", api_format: "bedrock" } };
    const second = { name: "bedrock", config: { baseUrl: "https://bedrock-runtime.eu-west-1.amazonaws.com", apiFormat: "bedrock" } };
    const view = render(withClient(<AddProviderDialog {...props} open provider={first} />));
    fireEvent.change(screen.getByLabelText(/^AWS Access Key ID/), { target: { value: "draft-access" } });
    fireEvent.change(screen.getByLabelText(/^AWS Secret Access Key/), { target: { value: "draft-secret" } });
    fireEvent.change(screen.getByLabelText(/^AWS Session Token/), { target: { value: "draft-session" } });
    fireEvent.change(screen.getByPlaceholderText(/model IDs/i), { target: { value: "draft-model" } });
    view.rerender(withClient(<AddProviderDialog {...props} open provider={second} />));
    expect(screen.getByLabelText(/^AWS Region/)).toHaveTextContent("eu-west-1");
    for (const label of [/^AWS Access Key ID/, /^AWS Secret Access Key/, /^AWS Session Token/]) {
      expect(screen.getByLabelText(label)).toHaveValue("");
    }
    expect(screen.getByPlaceholderText(/model IDs/i)).toHaveValue("");
    view.rerender(withClient(<AddProviderDialog {...props} open={false} provider={second} />));
    view.rerender(withClient(<AddProviderDialog {...props} open />));
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(screen.getByRole("option", { name: "AWS Bedrock" }));
    expect(screen.getByLabelText(/^AWS Region/)).toHaveTextContent("us-east-1");
    expect(screen.queryByText("saved-model")).toBeNull();
  });
});

describe("AddProviderDialog blank credential payloads", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchMutate.mockReset();
    fetchState.isPending = false;
    refreshState.failed = false;
    snackbars.length = 0;
  });

  it.each(["", "   ", " "])(
    "omits a blank API key (%j) while preserving the edited config",
    (blank) => {
      renderEditDialog({
        base_url: "https://saved.example.com",
        api_format: "openai",
        models: ["model-a"],
      });
      fireEvent.change(screen.getByLabelText(/^API Key/), {
        target: { value: blank },
      });
      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: "https://edited.example.com" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
      expect(updateMutate).toHaveBeenCalledTimes(1);
      expect(updateMutate.mock.calls[0][0]).toEqual({
        gatewayId: "gw-1",
        name: "openai",
        config: {
          base_url: "https://edited.example.com",
          api_format: "openai",
          api_path_prefix: "/v1",
          models: ["model-a"],
        },
      });
      expect(updateMutate.mock.calls[0][0].config).not.toHaveProperty(
        "api_key",
      );
    },
  );

  it.each(["typed-api", "  typed-api  "])(
    "probes and saves the same trimmed nonblank API key (%j)",
    (typed) => {
      vi.useFakeTimers();
      try {
        renderEditDialog({
          base_url: "https://saved.example.com",
          models: ["model-a"],
        });
        fireEvent.change(screen.getByLabelText(/^API Key/), {
          target: { value: typed },
        });
        act(() => vi.advanceTimersByTime(700));
        expect(fetchMutate).toHaveBeenCalledTimes(2);
        expect(fetchMutate.mock.calls[1][0].apiKey).toBe(typed.trim());
        fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
        expect(updateMutate).toHaveBeenCalledTimes(1);
        expect(updateMutate.mock.calls[0][0].config.api_key).toBe(typed.trim());
      } finally {
        vi.clearAllTimers();
        vi.useRealTimers();
      }
    },
  );

  it.each(["", "   ", " "])(
    "omits all blank AWS secrets (%j) without changing provider settings",
    (blank) => {
      renderEditDialogFor("bedrock", {
        base_url: "https://bedrock-runtime.us-east-1.amazonaws.com",
        api_format: "bedrock",
        models: ["model-a"],
      });
      for (const label of [
        /^AWS Access Key ID/,
        /^AWS Secret Access Key/,
        /^AWS Session Token/,
      ]) {
        fireEvent.change(screen.getByLabelText(label), {
          target: { value: blank },
        });
      }
      fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
      expect(updateMutate).toHaveBeenCalledTimes(1);
      expect(updateMutate.mock.calls[0][0]).toEqual({
        gatewayId: "gw-1",
        name: "bedrock",
        config: {
          base_url: "https://bedrock-runtime.us-east-1.amazonaws.com",
          api_format: "bedrock",
          aws_region: "us-east-1",
          models: ["model-a"],
        },
      });
      for (const field of [
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
      ]) {
        expect(updateMutate.mock.calls[0][0].config).not.toHaveProperty(field);
      }
    },
  );

  it("keeps nonblank AWS secrets byte-identical", () => {
    renderEditDialogFor("bedrock", {
      base_url: "https://bedrock-runtime.us-east-1.amazonaws.com",
      api_format: "bedrock",
      models: ["model-a"],
    });
    fireEvent.change(screen.getByLabelText(/^AWS Access Key ID/), {
      target: { value: "  example-access  " },
    });
    fireEvent.change(screen.getByLabelText(/^AWS Secret Access Key/), {
      target: { value: "example-secret" },
    });
    fireEvent.change(screen.getByLabelText(/^AWS Session Token/), {
      target: { value: "example-session" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config).toMatchObject({
      aws_access_key_id: "  example-access  ",
      aws_secret_access_key: "example-secret",
      aws_session_token: "example-session",
    });
  });

  it.each(["", "   ", " "])(
    "omits blank service-account JSON (%j) on an otherwise valid edit",
    (blank) => {
      renderEditDialogFor("vertex", {
        base_url:
          "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/demo-project/locations/us-central1",
        api_format: "gemini",
        models: ["model-a"],
      });
      fireEvent.change(screen.getByLabelText(/^Service-account JSON/), {
        target: { value: blank },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
      expect(updateMutate).toHaveBeenCalledTimes(1);
      expect(updateMutate.mock.calls[0][0]).toEqual({
        gatewayId: "gw-1",
        name: "vertex",
        config: {
          base_url:
            "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/demo-project/locations/us-central1",
          api_format: "gemini",
          gcp_project: "demo-project",
          gcp_location: "us-central1",
          models: ["model-a"],
        },
      });
      expect(updateMutate.mock.calls[0][0].config).not.toHaveProperty(
        "service_account_json",
      );
    },
  );
});

describe("AddProviderDialog presets from the hosts registry", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchMutate.mockReset();
    fetchState.isPending = false;
    refreshState.failed = false;
    snackbars.length = 0;
  });

  it("lists the providers in groups, with every host the gateway can route to", async () => {
    renderCreateDialog();
    await userEvent.click(screen.getByLabelText(/^Provider/));

    for (const group of [
      "Model providers",
      "Inference hosts",
      "Routers and gateways",
      "Other",
    ]) {
      expect(screen.getByText(group)).toBeInTheDocument();
    }
    for (const name of [
      "Cerebras",
      "DeepInfra",
      "Hugging Face",
      "xAI",
      "Vercel AI Gateway",
      "Custom / Self-hosted",
    ]) {
      expect(screen.getByRole("option", { name })).toBeInTheDocument();
    }
    // Hosts whose vendors document no route the gateway can use.
    expect(screen.queryByRole("option", { name: "Perplexity" })).toBeNull();
    expect(screen.queryByRole("option", { name: "Anyscale" })).toBeNull();
  });

  it("fills a registry host's endpoint, path prefix and key hint", async () => {
    renderCreateDialog();
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(screen.getByRole("option", { name: "Together AI" }));

    expect(connectionField(/^Base URL/)).toHaveValue(
      "https://api.together.ai",
    );
    expect(connectionField("API Path Prefix")).toHaveValue("/v1");

    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(screen.getByRole("option", { name: "DeepInfra" }));
    expect(connectionField(/^Base URL/)).toHaveValue(
      "https://api.deepinfra.com",
    );
    expect(connectionField("API Path Prefix")).toHaveValue("/v1/openai");
  });

  it("links the vendor's docs and says who bills a request on the org's key", async () => {
    renderCreateDialog();
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(screen.getByRole("option", { name: "Groq" }));

    expect(screen.getByRole("link", { name: /Groq docs/ })).toHaveAttribute(
      "href",
      "https://console.groq.com/docs/api-reference",
    );
    expect(
      screen.getByText(/Requests use your own Groq key\./),
    ).toBeInTheDocument();
  });

  it("keeps Azure on its one wire format", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const warnings = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderCreateDialog();
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(screen.getByRole("option", { name: "Azure OpenAI" }));

    expect(connectionField(/^API Format/)).toHaveTextContent("azure");
    const outOfRange = [...errors.mock.calls, ...warnings.mock.calls].filter(
      (call) => String(call[0]).includes("out-of-range"),
    );
    expect(outOfRange).toEqual([]);
    errors.mockRestore();
    warnings.mockRestore();
  });
});

describe("getApiPathPrefix", () => {
  it("preserves an explicitly empty prefix", () => {
    expect(getApiPathPrefix({ api_path_prefix: "" })).toBe("");
  });

  it("uses the default for providers saved before the field existed", () => {
    expect(getApiPathPrefix({})).toBe(DEFAULT_API_PATH_PREFIX);
  });

  it("includes an explicit empty prefix in the saved OpenAI config", () => {
    expect(
      withApiPathPrefix(
        { base_url: "https://api.perplexity.ai" },
        "openai",
        "",
      ),
    ).toEqual({
      base_url: "https://api.perplexity.ai",
      api_path_prefix: "",
    });
  });

  it("does not add a path prefix for non-OpenAI formats", () => {
    // A non-empty prefix, so the branch has a real value to drop rather than a
    // blank that would look absent either way.
    const config = { base_url: "https://api.anthropic.com" };
    expect(withApiPathPrefix(config, "anthropic", "/v1")).toEqual(config);
    expect(withApiPathPrefix(config, "anthropic", "/v1")).not.toHaveProperty(
      "api_path_prefix",
    );
  });
});

describe("AddProviderDialog saved Base URL guidance", () => {
  const note =
    "Model loading uses the saved Base URL. Save the URL change, then reopen this dialog to load models, or enter an API key to test the edited URL before saving.";

  beforeEach(() => {
    updateMutate.mockReset();
    fetchMutate.mockReset();
    fetchState.isPending = false;
    refreshState.failed = false;
    snackbars.length = 0;
  });

  it("shows the exact guidance only while an empty-key edit changes the saved URL", () => {
    renderEditDialog({
      base_url: "https://saved.example.com",
      api_format: "openai",
      models: ["model-a"],
    });
    expect(screen.queryByText(note)).toBeNull();

    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://edited.example.com" },
    });
    expect(
      screen.getByText(
        "Model loading uses the saved Base URL. Save the URL change, then reopen this dialog to load models, or enter an API key to test the edited URL before saving.",
      ),
    ).toBeInTheDocument();

    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://saved.example.com" },
    });
    expect(screen.queryByText(note)).toBeNull();
  });

  it.each([
    { base_url: "https://saved.example.com" },
    { baseUrl: "https://saved.example.com" },
    {
      base_url: "https://saved.example.com",
      baseUrl: "https://ignored.example.com",
    },
  ])("compares trimmed URLs using the saved field precedence: %j", (config) => {
    renderEditDialog({ ...config, models: ["model-a"] });
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "  https://saved.example.com  " },
    });
    expect(screen.queryByText(note)).toBeNull();
  });

  it.each(["systemone", "typesafe"])(
    "does not promise model loading for a %s provider",
    (format) => {
      renderEditDialogFor("custom-systemone", {
        base_url: "https://saved.example.com",
        api_format: format,
        models: ["strands-decider"],
      });
      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: "https://edited.example.com" },
      });
      expect(screen.queryByText(note)).toBeNull();
      expect(fetchMutate).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
      expect(updateMutate.mock.calls[0][0].config).not.toHaveProperty(
        "api_key",
      );
      expect(updateMutate.mock.calls[0][0].config.api_format).toBe("systemone");
    },
  );

  it("does not normalize a trailing slash or path difference away", () => {
    renderEditDialog({
      base_url: "https://saved.example.com",
      models: ["model-a"],
    });
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://saved.example.com/" },
    });
    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it.each(["replacement-key", "   "])(
    "hides guidance for a nonempty key input (%j), restoring it only on exact empty",
    (key) => {
      renderEditDialog({
        base_url: "https://saved.example.com",
        models: ["model-a"],
      });
      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: "https://edited.example.com" },
      });
      fireEvent.change(screen.getByLabelText(/^API Key/), {
        target: { value: key },
      });
      expect(screen.queryByText(note)).toBeNull();
      fireEvent.change(screen.getByLabelText(/^API Key/), {
        target: { value: "" },
      });
      expect(screen.getByText(note)).toBeInTheDocument();
    },
  );

  it("does not show edit guidance in add mode", () => {
    renderCreateDialog();
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://edited.example.com" },
    });
    expect(screen.queryByText(note)).toBeNull();
  });

  it.each(["bedrock", "vertex"])("hides guidance for %s auth", (name) => {
    renderEditDialogFor(name, {
      base_url: "https://saved.example.com",
      models: ["model-a"],
    });
    if (name === "bedrock") {
      fireEvent.mouseDown(screen.getByLabelText(/^AWS Region/));
      fireEvent.click(screen.getByRole("option", { name: /us-west-2/ }));
    } else {
      fireEvent.change(screen.getByLabelText(/^Google Cloud project ID/), {
        target: { value: "edited-project" },
      });
    }
    expect(screen.queryByLabelText(/^Base URL/)).toBeNull();
    expect(screen.queryByText(note)).toBeNull();
  });

  it("hides guidance when the edited URL is empty", () => {
    renderEditDialog({
      base_url: "https://saved.example.com",
      models: ["model-a"],
    });
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "   " },
    });
    expect(screen.queryByText(note)).toBeNull();
  });

  it("hides guidance when the stored URL selects a default endpoint", () => {
    renderEditDialog({ models: ["model-a"] });
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://edited.example.com" },
    });
    expect(screen.queryByText(note)).toBeNull();
  });

  it.each(["google", "gemini", "google_gemini"])(
    "does not claim %s uses its saved URL in OpenAI-format catalogue lookup",
    (name) => {
      renderEditDialogFor(name, {
        base_url: "https://saved.example.com",
        api_format: "openai",
        models: ["model-a"],
      });
      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: "https://edited.example.com" },
      });
      expect(screen.queryByText(note)).toBeNull();
    },
  );

  it("shows native Google URL guidance but hides it when Save would select the default catalogue", () => {
    renderEditDialogFor("google", {
      base_url: "https://saved.example.com",
      api_format: "gemini",
      models: ["model-a"],
    });
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://edited.example.com" },
    });
    expect(screen.getByText(note)).toBeInTheDocument();
    fireEvent.mouseDown(connectionField(/^API Format/));
    fireEvent.click(screen.getByRole("option", { name: "openai" }));
    expect(screen.queryByText(note)).toBeNull();
  });

  it("never sends the edited URL through the stored-key path, including a prefix refetch", () => {
    vi.useFakeTimers();
    try {
      renderEditDialogFor("custom", {
        base_url: "https://saved.example.com",
        api_format: "openai",
        api_path_prefix: "/v1",
        models: ["model-a"],
      });
      expect(fetchMutate).toHaveBeenCalledTimes(1);
      expect(fetchMutate.mock.calls[0][0]).toEqual({
        providerName: "custom",
        apiFormat: "openai",
        apiPathPrefix: "/v1",
      });

      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: "https://edited.example.com" },
      });
      act(() => vi.advanceTimersByTime(700));
      expect(fetchMutate).toHaveBeenCalledTimes(1);
      expect(screen.getByText(note)).toBeInTheDocument();

      fireEvent.change(connectionField("API Path Prefix"), {
        target: { value: "/discovery" },
      });
      act(() => vi.advanceTimersByTime(700));
      expect(fetchMutate).toHaveBeenCalledTimes(2);
      expect(fetchMutate.mock.calls[1][0]).toEqual({
        providerName: "custom",
        apiFormat: "openai",
        apiPathPrefix: "/discovery",
      });
      for (const [payload] of fetchMutate.mock.calls) {
        expect(payload).not.toHaveProperty("baseUrl");
        expect(payload).not.toHaveProperty("base_url");
        expect(payload).not.toHaveProperty("apiKey");
        expect(payload).not.toHaveProperty("api_key");
      }

      fireEvent.change(screen.getByLabelText(/^API Key/), {
        target: { value: "replacement-key" },
      });
      act(() => vi.advanceTimersByTime(700));
      expect(fetchMutate).toHaveBeenCalledTimes(3);
      expect(fetchMutate.mock.calls[2][0]).toEqual({
        providerName: "custom",
        baseUrl: "https://edited.example.com",
        apiKey: "replacement-key",
        apiFormat: "openai",
        apiPathPrefix: "/discovery",
      });
      expect(screen.queryByText(note)).toBeNull();
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
    }
  });

  it("omits the key on Save and requests by name when reopened with the saved URL", () => {
    const view = renderEditDialog({
      base_url: "https://saved.example.com",
      api_format: "openai",
      models: ["model-a"],
    });
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: "https://edited.example.com" },
    });
    expect(screen.getByText(note)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].config).toEqual({
      base_url: "https://edited.example.com",
      api_format: "openai",
      api_path_prefix: "/v1",
      models: ["model-a"],
    });
    view.unmount();
    fetchMutate.mockClear();
    renderEditDialog({
      base_url: "https://edited.example.com",
      api_format: "openai",
      models: ["model-a"],
    });
    expect(fetchMutate).toHaveBeenCalledTimes(1);
    expect(fetchMutate.mock.calls[0][0]).toEqual({
      providerName: "openai",
      apiFormat: "openai",
      apiPathPrefix: "/v1",
    });
    expect(screen.queryByText(note)).toBeNull();
  });
});

describe("AddProviderDialog model discovery", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchState.isPending = false;
    fetchMutate.mockReset();
    fetchMutate.mockImplementation(
      deferred((_vars, opts) => opts?.onSuccess?.({ models: ["sonar"] })),
    );
  });

  it("lists models against the stored provider's own path prefix", async () => {
    renderEditDialogFor("perplexity", {
      base_url: "https://api.perplexity.ai",
      api_format: "openai",
      api_path_prefix: "/openai/v1",
      models: ["sonar"],
    });

    await waitFor(() => expect(fetchMutate).toHaveBeenCalled());
    expect(fetchMutate.mock.calls[0][0]).toMatchObject({
      providerName: "perplexity",
      apiFormat: "openai",
      apiPathPrefix: "/openai/v1",
    });
  });

  it("lists again when the prefix is edited, with the stored key untouched", async () => {
    // The debounced key effect returns early on a blank key in edit mode, so
    // without its own effect this change would never reach the provider.
    renderEditDialogFor("perplexity", {
      base_url: "https://api.perplexity.ai",
      api_format: "openai",
      api_path_prefix: "/v1",
      models: ["sonar"],
    });
    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(1));

    const prefix = connectionField("API Path Prefix");
    await userEvent.clear(prefix);

    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(2), {
      timeout: 3000,
    });
    expect(fetchMutate.mock.calls[1][0]).toMatchObject({
      providerName: "perplexity",
      apiPathPrefix: "",
    });
    // A blank key still means "keep the stored one", so none is sent.
    expect(fetchMutate.mock.calls[1][0].apiKey).toBeUndefined();
  });

  it("does not list again when the dialog merely hydrates the prefix", async () => {
    renderEditDialogFor("perplexity", {
      base_url: "https://api.perplexity.ai",
      api_format: "openai",
      api_path_prefix: "/openai/v1",
      models: ["sonar"],
    });

    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 900));
    expect(fetchMutate).toHaveBeenCalledTimes(1);
  });

  it("carries the prefix on a typed-key fetch", async () => {
    renderCreateDialog();

    const prefix = connectionField("API Path Prefix");
    await userEvent.clear(prefix);
    await userEvent.type(prefix, "/openai/v1");
    fireEvent.change(screen.getByLabelText(/^API Key/), {
      target: { value: "sk-test" },
    });

    await waitFor(() => expect(fetchMutate).toHaveBeenCalled(), {
      timeout: 3000,
    });
    const last = fetchMutate.mock.calls[fetchMutate.mock.calls.length - 1][0];
    expect(last).toMatchObject({
      apiKey: "sk-test",
      apiFormat: "openai",
      apiPathPrefix: "/openai/v1",
    });
  });

  it("presets OpenRouter's endpoint and lists its models with the typed key", async () => {
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: ["anthropic/claude-sonnet-5.5"] }),
      ),
    );
    renderCreateDialog();
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(screen.getByRole("option", { name: "OpenRouter" }));

    expect(connectionField(/^Base URL/)).toHaveValue(
      "https://openrouter.ai/api",
    );
    expect(connectionField("API Path Prefix")).toHaveValue("/v1");
    expect(screen.getByLabelText(/^API Key/)).toHaveAttribute(
      "placeholder",
      "sk-or-...",
    );
    expect(
      screen.getByText(/Requests use your own OpenRouter key\./),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^API Key/), {
      target: { value: "sk-or-test" },
    });
    await waitFor(() => expect(fetchMutate).toHaveBeenCalled(), {
      timeout: 3000,
    });
    expect(fetchMutate.mock.calls.at(-1)[0]).toMatchObject({
      baseUrl: "https://openrouter.ai/api",
      apiKey: "sk-or-test",
      apiFormat: "openai",
      apiPathPrefix: "/v1",
    });

    const models = await screen.findByPlaceholderText(/Select models/i);
    // Escape closes the list first: with it open, the blur from clicking Add
    // Provider auto-selects the highlighted option, which un-picks it.
    await userEvent.type(models, "anthropic/claude-sonnet-5.5{enter}{escape}");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    expect(updateMutate).toHaveBeenCalledTimes(1);
    const payload = updateMutate.mock.calls[0][0];
    expect(payload.name).toBe("openrouter");
    expect(payload.config).toMatchObject({
      base_url: "https://openrouter.ai/api",
      api_format: "openai",
      api_path_prefix: "/v1",
      api_key: "sk-or-test",
      models: ["anthropic/claude-sonnet-5.5"],
    });
  });

  it("tags a catalogue model but never a hand-typed one the catalogue didn't list", async () => {
    // Perplexity's catalogue never lists `sonar`/`sonar-pro` — those are
    // typed by hand for chat completions — but it does list aggregator IDs
    // like `openai/gpt-4o`, which must still get tagged.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: ["openai/gpt-4o"] }),
      ),
    );
    renderEditDialogFor("perplexity", {
      base_url: "https://api.perplexity.ai",
      api_format: "openai",
      api_path_prefix: "/v1",
      models: ["sonar", "openai/gpt-4o"],
    });

    await waitFor(() => expect(fetchMutate).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getAllByText(RESPONSES_ONLY_TAG).length).toBe(1),
    );

    const sonarChip = screen
      .getByText("sonar")
      .closest("li, [class*='MuiChip']");
    expect(sonarChip?.textContent).not.toContain(RESPONSES_ONLY_TAG);

    const catalogueChip = screen
      .getByText("openai/gpt-4o")
      .closest("li, [class*='MuiChip']");
    expect(catalogueChip?.textContent).toContain(RESPONSES_ONLY_TAG);
  });
});

describe("API format labels", () => {
  it("names System One, and its typesafe alias, for what it is", () => {
    expect(apiFormatLabel("systemone")).toBe("System One (decision models)");
    expect(apiFormatLabel("typesafe")).toBe("System One (decision models)");
  });

  it("leaves the chat formats as their IDs", () => {
    expect(apiFormatLabel("openai")).toBe("openai");
    expect(apiFormatLabel("anthropic")).toBe("anthropic");
  });

  it("passes an empty format through so the caller's placeholder shows", () => {
    expect(apiFormatLabel(undefined)).toBeUndefined();
    expect(apiFormatLabel("")).toBe("");
  });

  it("maps only the typesafe alias onto System One", () => {
    expect(normalizeApiFormat("typesafe")).toBe("systemone");
    expect(normalizeApiFormat("openai")).toBe("openai");
    expect(isSystemOneFormat("typesafe")).toBe(true);
    expect(isSystemOneFormat("systemone")).toBe(true);
    expect(isSystemOneFormat("openai")).toBe(false);
    expect(isSystemOneFormat(undefined)).toBe(false);
  });

  it("never adds a path prefix to a System One config", () => {
    // The dialect row owns the leaf path; the prefix stays the default.
    const config = { base_url: "https://api.typesafe.ai" };
    expect(withApiPathPrefix(config, "systemone", "/v1")).toEqual(config);
  });
});

describe("AddProviderDialog System One presets", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchState.isPending = false;
    refreshState.failed = false;
    snackbars.length = 0;
    fetchMutate.mockReset();
    // Would list chat models if anything asked; nothing should.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) => opts?.onSuccess?.({ models: ["gpt-4o"] })),
    );
  });

  // Longer than the dialog's 600ms model-fetch debounce.
  const pastDebounce = () =>
    act(() => new Promise((resolve) => setTimeout(resolve, 900)));

  const pickPreset = async (label) => {
    await userEvent.click(screen.getByRole("combobox", { name: /^Provider/ }));
    await userEvent.click(screen.getByRole("option", { name: label }));
  };

  const apiFormatField = () => connectionField(/^API Format/);

  const chipLabels = () =>
    Array.from(document.querySelectorAll(".MuiChip-label")).map(
      (el) => el.textContent,
    );

  it.each([
    {
      label: "TypeSafe",
      name: "typesafe",
      baseUrl: "https://api.typesafe.ai",
      models: ["jev-1.13.0", "jev-latest"],
    },
    {
      label: "Perplexity Decider",
      name: "perplexity-decider",
      baseUrl: "https://api.perplexity.ai",
      models: ["pplx-decider-v1.1-27b", "pplx-decider-v1-27b"],
    },
  ])(
    "$label saves System One with its endpoint and built-in models, never probing",
    async ({ label, name, baseUrl, models }) => {
      renderCreateDialog();
      await pickPreset(label);

      expect(connectionField(/^Base URL/).value).toBe(baseUrl);
      expect(apiFormatField().textContent).toBe("System One (decision models)");
      expect(apiFormatField().getAttribute("aria-disabled")).toBe("true");
      // System One has no path prefix to set.
      expect(screen.queryByLabelText("API Path Prefix")).toBeNull();
      // Offered and already selected: there is no listing to wait for.
      expect(chipLabels()).toEqual(models);
      expect(screen.getByText(SYSTEM_ONE_MODELS_HELP)).toBeTruthy();

      await userEvent.type(screen.getByLabelText(/^API Key/), "key-123");
      await pastDebounce();
      expect(fetchMutate).not.toHaveBeenCalled();

      await userEvent.click(
        screen.getByRole("button", { name: "Add provider" }),
      );
      expect(fetchMutate).not.toHaveBeenCalled();
      expect(updateMutate).toHaveBeenCalledTimes(1);
      expect(updateMutate.mock.calls[0][0]).toEqual({
        gatewayId: "gw-1",
        name,
        config: {
          base_url: baseUrl,
          api_format: "systemone",
          api_key: "key-123",
          models,
        },
      });
    },
  );

  it("does not read the Perplexity Decider as Perplexity's Responses catalogue", async () => {
    // Same host as the chat API, whose catalogue is tagged Responses-only.
    renderCreateDialog();
    await pickPreset("Perplexity Decider");

    expect(screen.queryByText(RESPONSES_ONLY_TAG)).toBeNull();
    // So Select All still covers its models.
    expect(screen.getByRole("button", { name: "Deselect All" })).toBeTruthy();
  });

  it("requires a base URL for a custom System One endpoint", async () => {
    renderCreateDialog();
    await pickPreset("Custom System One endpoint");

    const baseUrl = connectionField(/^Base URL/);
    expect(baseUrl.value).toBe("");
    expect(baseUrl.required).toBe(true);
    expect(apiFormatField().textContent).toBe("System One (decision models)");
    expect(
      screen.getByText(/the gateway posts to this URL plus \/v1\/systemone/),
    ).toBeTruthy();
    // No built-in list for an endpoint we know nothing about.
    expect(chipLabels()).toEqual([]);

    await userEvent.type(screen.getByLabelText(/^API Key/), "key-123");
    await userEvent.type(
      screen.getByPlaceholderText(/comma-separated model IDs/i),
      "strands-decider{enter}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(
      screen.getAllByText(/This provider has no preset endpoint/).length,
    ).toBeGreaterThan(0);
  });

  it("rejects a custom System One base URL that is not http(s)", async () => {
    renderCreateDialog();
    await pickPreset("Custom System One endpoint");

    await userEvent.type(
      connectionField(/^Base URL/),
      "ftp://decider.example.com",
    );
    await userEvent.type(screen.getByLabelText(/^API Key/), "key-123");
    await userEvent.type(
      screen.getByPlaceholderText(/comma-separated model IDs/i),
      "strands-decider{enter}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    expect(updateMutate).not.toHaveBeenCalled();
    expect(
      screen.getAllByText(/Base URL must start with http:\/\/ or https:\/\//)
        .length,
    ).toBeGreaterThan(0);
  });

  it.each(["", "   "])(
    "saves a keyless custom System One endpoint with clear optional-key help (key %j)",
    async (apiKey) => {
      renderCreateDialog();
      await pickPreset("Custom System One endpoint");
      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: "https://decider.example.com" },
      });
      fireEvent.change(screen.getByLabelText(/^API Key/), {
        target: { value: apiKey },
      });
      expect(screen.getByLabelText(/^API Key/).required).toBe(false);
      expect(
        screen.getByText(/Optional for a self-hosted System One endpoint/),
      ).toBeTruthy();
      await userEvent.type(
        screen.getByPlaceholderText(/comma-separated model IDs/i),
        "strands-decider{enter}",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Add provider" }),
      );

      expect(updateMutate).toHaveBeenCalledTimes(1);
      expect(updateMutate.mock.calls[0][0]).toEqual({
        gatewayId: "gw-1",
        name: "custom-systemone",
        config: {
          base_url: "https://decider.example.com",
          api_format: "systemone",
          models: ["strands-decider"],
        },
      });
      expect(fetchMutate).not.toHaveBeenCalled();
    },
  );

  it.each([
    "",
    "https://",
    "https://bad host.invalid",
    "https://decider.example.com:invalid",
  ])("refuses a keyless endpoint with invalid Base URL %j", async (baseUrl) => {
    renderCreateDialog();
    await pickPreset("Custom System One endpoint");
    fireEvent.change(connectionField(/^Base URL/), {
      target: { value: baseUrl },
    });
    expect(screen.getByLabelText(/^API Key/).required).toBe(true);
    await userEvent.type(
      screen.getByPlaceholderText(/comma-separated model IDs/i),
      "strands-decider{enter}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(updateMutate).not.toHaveBeenCalled();
    expect(
      connectionField(/^Base URL/).getAttribute("aria-invalid"),
    ).toBe("true");
  });

  it.each(["TypeSafe", "Perplexity Decider", "OpenAI"])(
    "still requires an API key for %s",
    async (label) => {
      renderCreateDialog();
      await pickPreset(label);
      expect(screen.getByLabelText(/^API Key/).required).toBe(true);
      if (label === "OpenAI") {
        await userEvent.type(
          screen.getByPlaceholderText(/type manually/i),
          "gpt-4o{enter}",
        );
      }
      await userEvent.click(
        screen.getByRole("button", { name: "Add provider" }),
      );
      expect(updateMutate).not.toHaveBeenCalled();
      expect(screen.getAllByText("API key is required").length).toBeGreaterThan(
        0,
      );
    },
  );

  it.each([
    "https://API.TypeSafe.AI./v1",
    "https://api.perplexity.ai",
    "https://api.openai.com/v1",
  ])(
    "requires a key when a custom System One endpoint uses vendor URL %s",
    async (baseUrl) => {
      renderCreateDialog();
      await pickPreset("Custom System One endpoint");
      fireEvent.change(connectionField(/^Base URL/), {
        target: { value: baseUrl },
      });
      expect(screen.getByLabelText(/^API Key/).required).toBe(true);
      expect(screen.queryByText(/Optional for a self-hosted/)).toBeNull();
      await userEvent.type(
        screen.getByPlaceholderText(/comma-separated model IDs/i),
        "strands-decider{enter}",
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Add provider" }),
      );
      expect(updateMutate).not.toHaveBeenCalled();
    },
  );

  it("resaves a keyless custom System One provider without adding a key", async () => {
    renderEditDialogFor("custom-systemone", {
      base_url: "https://decider.example.com",
      api_format: "systemone",
      models: ["strands-decider"],
    });
    expect(screen.getByText(/Optional for a self-hosted/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config).not.toHaveProperty("api_key");
    expect(fetchMutate).not.toHaveBeenCalled();
  });

  it("saves a custom System One endpoint with the URL and typed model", async () => {
    renderCreateDialog();
    await pickPreset("Custom System One endpoint");

    await userEvent.type(
      connectionField(/^Base URL/),
      "https://decider.example.com",
    );
    await userEvent.type(screen.getByLabelText(/^API Key/), "key-123");
    await pastDebounce();
    await userEvent.type(
      screen.getByPlaceholderText(/comma-separated model IDs/i),
      "strands-decider{enter}",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    expect(fetchMutate).not.toHaveBeenCalled();
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toMatchObject({
      name: "custom-systemone",
      config: {
        base_url: "https://decider.example.com",
        api_format: "systemone",
        api_key: "key-123",
        models: ["strands-decider"],
      },
    });
    expect(updateMutate.mock.calls[0][0].config).not.toHaveProperty(
      "api_path_prefix",
    );
  });

  it("goes back to a chat format and an empty list when a chat preset is picked", async () => {
    renderCreateDialog();
    await pickPreset("TypeSafe");
    await pickPreset("OpenAI");

    expect(apiFormatField().textContent).toBe("openai");
    expect(chipLabels()).toEqual([]);
    expect(connectionField(/^Base URL/).value).toBe(
      "https://api.openai.com/v1",
    );
  });

  it("does not offer System One for a chat preset", async () => {
    renderCreateDialog();
    await userEvent.click(apiFormatField());

    const formats = screen.getAllByRole("option").map((o) => o.textContent);
    expect(formats).toEqual(["openai", "anthropic"]);
  });

  it("opens a saved TypeSafe provider without listing models", async () => {
    renderEditDialogFor("typesafe", {
      base_url: "https://api.typesafe.ai",
      api_format: "systemone",
      models: ["jev-1.13.0"],
    });
    await pastDebounce();

    expect(fetchMutate).not.toHaveBeenCalled();
    expect(apiFormatField().textContent).toBe("System One (decision models)");
    // The other built-in model is still on offer.
    await userEvent.click(screen.getByRole("button", { name: "Select All" }));
    expect(chipLabels()).toEqual(["jev-1.13.0", "jev-latest"]);

    const save = screen.getByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(false);
    await userEvent.click(save);
    expect(updateMutate.mock.calls[0][0].config).toEqual({
      base_url: "https://api.typesafe.ai",
      api_format: "systemone",
      models: ["jev-1.13.0", "jev-latest"],
    });
  });

  it("saves the typesafe alias as System One", async () => {
    renderEditDialogFor("typesafe", {
      base_url: "https://api.typesafe.ai",
      api_format: "typesafe",
      models: ["jev-latest"],
    });

    expect(apiFormatField().textContent).toBe("System One (decision models)");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config.api_format).toBe("systemone");
    expect(fetchMutate).not.toHaveBeenCalled();
  });

  it("repairs a decision preset that was saved with a chat format", async () => {
    // The admin API defaults api_format to openai, which would make TypeSafe a
    // chat provider; the dialog must not show it as one, or probe it.
    renderEditDialogFor("perplexity-decider", {
      base_url: "https://api.perplexity.ai",
      api_format: "openai",
      models: ["pplx-decider-v1.1-27b"],
    });

    expect(apiFormatField().textContent).toBe("System One (decision models)");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config.api_format).toBe("systemone");
    expect(fetchMutate).not.toHaveBeenCalled();
  });

  it("never probes a saved custom System One endpoint, even with a new key", async () => {
    renderEditDialogFor("custom-systemone", {
      base_url: "https://decider.example.com",
      api_format: "systemone",
      models: ["strands-decider"],
    });

    await userEvent.type(screen.getByLabelText(/^API Key/), "key-rotated");
    await pastDebounce();

    expect(fetchMutate).not.toHaveBeenCalled();
    const save = screen.getByRole("button", { name: "Save Changes" });
    expect(save.disabled).toBe(false);
    await userEvent.click(save);
    expect(updateMutate.mock.calls[0][0].config).toMatchObject({
      api_format: "systemone",
      api_key: "key-rotated",
      models: ["strands-decider"],
    });
  });
});

describe("AddProviderDialog OpenAI decision models", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchState.isPending = false;
    fetchMutate.mockReset();
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: ["gpt-4o", "gpt-4o-mini"] }),
      ),
    );
  });

  it("offers gpt-6-luna beside OpenAI's listing, tagged and out of Select All", async () => {
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/^API Key/), "sk-good");
    const input = await screen.findByPlaceholderText(/Select models/i);
    // The count stays the listing's own.
    expect(screen.getByText("(2 available)")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Select All" }));
    expect(
      Array.from(document.querySelectorAll(".MuiChip-label")).map(
        (el) => el.textContent,
      ),
    ).toEqual(["gpt-4o", "gpt-4o-mini"]);

    await userEvent.click(input);
    const luna = await screen.findByRole("option", { name: /gpt-6-luna/ });
    expect(luna.textContent).toContain(DECISIONS_ONLY_TAG);
    expect(
      screen.getByRole("option", { name: /gpt-4o-mini/ }).textContent,
    ).not.toContain(DECISIONS_ONLY_TAG);

    await userEvent.click(luna);
    // Closed first: with autoSelect, blurring an open list toggles the
    // highlighted option again.
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(updateMutate.mock.calls[0][0].config.models).toEqual([
      "gpt-4o",
      "gpt-4o-mini",
      "gpt-6-luna",
    ]);
  });

  it("does not let gpt-6-luna stand in for a listing that came back empty", async () => {
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({
          models: [],
          error: "Failed to fetch models from provider",
        }),
      ),
    );
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/^API Key/), "sk-bad");
    await screen.findByText(/check that it is valid for this provider/i);

    await userEvent.click(screen.getByPlaceholderText(/type manually/i));
    expect(screen.queryByRole("option", { name: /gpt-6-luna/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Add provider" }).disabled).toBe(
      true,
    );
  });

  it("keeps the gate for a rejected key after gpt-6-luna was picked", async () => {
    // Picked from the list, so it is not a hand-typed override.
    let reject = false;
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.(
          reject
            ? { models: [], error: "Failed to fetch models from provider" }
            : { models: ["gpt-4o"] },
        ),
      ),
    );
    renderCreateDialog();

    await userEvent.type(screen.getByLabelText(/^API Key/), "sk-good");
    const input = await screen.findByPlaceholderText(/Select models/i);
    await userEvent.click(input);
    await userEvent.click(
      await screen.findByRole("option", { name: /gpt-6-luna/ }),
    );
    await userEvent.keyboard("{Escape}");
    // Selected, with the tag carried into its chip.
    expect(
      Array.from(document.querySelectorAll(".MuiChip-label")).map(
        (el) => el.textContent,
      ),
    ).toEqual([`gpt-6-luna${DECISIONS_ONLY_TAG}`]);

    reject = true;
    await userEvent.clear(screen.getByLabelText(/^API Key/));
    await userEvent.type(screen.getByLabelText(/^API Key/), "sk-rejected");

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add provider" }).disabled,
      ).toBe(true),
    );
    expect(updateMutate).not.toHaveBeenCalled();
  });
});

describe("AddProviderDialog provider names", () => {
  beforeEach(() => {
    updateMutate.mockReset();
    fetchState.isPending = false;
    refreshState.failed = false;
    snackbars.length = 0;
    fetchMutate.mockReset();
    fetchMutate.mockImplementation(
      deferred((_vars, opts) => opts?.onSuccess?.({ models: ["m-1"] })),
    );
  });

  const renderWithNames = (existingNames) =>
    render(
      withClient(
        <AddProviderDialog
          open
          onClose={vi.fn()}
          gatewayId="gw-1"
          existingNames={existingNames}
        />,
      ),
    );

  const addModel = async (model) => {
    await userEvent.type(
      await screen.findByPlaceholderText(/Select models|type manually/i),
      `${model}{enter}`,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add provider" }).disabled,
      ).toBe(false),
    );
  };

  const fillCustom = async (customName) => {
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(
      screen.getByRole("option", { name: "Custom / Self-hosted" }),
    );
    const nameField = screen.getByLabelText(/^Name/);
    await userEvent.clear(nameField);
    if (customName) await userEvent.type(nameField, customName);
    await userEvent.type(
      connectionField(/Base URL/),
      "https://api.perplexity.ai",
    );
    await userEvent.type(screen.getByLabelText(/API Key/i), "pplx-test");
    await waitFor(() => expect(fetchMutate).toHaveBeenCalled());
    await addModel("sonar");
  };

  it("saves a custom provider under the name typed, next to an existing one", async () => {
    // The second Perplexity API needs its own provider; it used to replace
    // the first, because every custom provider was saved as "custom".
    renderWithNames(["custom", "openai"]);
    await fillCustom("perplexity-sonar");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate.mock.calls[0][0].name).toBe("perplexity-sonar");
    expect(updateMutate.mock.calls[0][0].config.base_url).toBe(
      "https://api.perplexity.ai",
    );
  });

  it("saves Perplexity on its Agent API endpoint through Custom / Self-hosted", async () => {
    // The composed picker has no Perplexity preset. Keep the endpoint and
    // path-prefix submission contract through the supported custom flow.
    fetchMutate.mockImplementation(
      deferred((_vars, opts) =>
        opts?.onSuccess?.({ models: ["perplexity/sonar"] }),
      ),
    );
    renderWithNames([]);
    await userEvent.click(screen.getByLabelText(/^Provider/));
    await userEvent.click(
      screen.getByRole("option", { name: "Custom / Self-hosted" }),
    );
    await userEvent.clear(screen.getByLabelText(/^Name/));
    await userEvent.type(screen.getByLabelText(/^Name/), "perplexity");
    await userEvent.type(
      connectionField(/Base URL/),
      "https://api.perplexity.ai",
    );
    await userEvent.clear(connectionField("API Path Prefix"));
    await userEvent.type(connectionField("API Path Prefix"), "/v1");
    await userEvent.type(screen.getByLabelText(/API Key/i), "pplx-test");

    await waitFor(() => expect(fetchMutate).toHaveBeenCalledTimes(1), {
      timeout: DEBOUNCED_WAIT,
    });
    expect(fetchMutate.mock.calls[0][0]).toEqual({
      baseUrl: "https://api.perplexity.ai",
      apiKey: "pplx-test",
      apiFormat: "openai",
      apiPathPrefix: "/v1",
    });
    await userEvent.type(
      await screen.findByPlaceholderText(/Select models|type manually/i),
      "perplexity/sonar{enter}{escape}",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Add provider" }),
      ).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate.mock.calls[0][0]).toEqual({
      gatewayId: "gw-1",
      name: "perplexity",
      config: {
        base_url: "https://api.perplexity.ai",
        api_format: "openai",
        api_path_prefix: "/v1",
        api_key: "pplx-test",
        models: ["perplexity/sonar"],
      },
    });
  });

  it("treats an object method name as an ordinary name", async () => {
    renderWithNames([]);
    await fillCustom("constructor");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate.mock.calls[0][0].name).toBe("constructor");
  });

  it("keeps the old name for a first custom provider", async () => {
    renderWithNames(["openai"]);
    await fillCustom("custom");
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalledTimes(1));
    expect(updateMutate.mock.calls[0][0].name).toBe("custom");
  });

  it.each([
    ["custom", /A provider named "custom" already exists/],
    ["openai", /"openai" is a built-in provider/],
    ["Perplexity Sonar", /Use lowercase letters, digits, - and _/],
    ["", /Name is required/],
  ])("refuses the name %j", async (customName, message) => {
    renderWithNames(["custom"]);
    await fillCustom(customName);
    await userEvent.click(screen.getByRole("button", { name: "Add provider" }));

    expect((await screen.findAllByText(message)).length).toBeGreaterThan(0);
    expect(updateMutate).not.toHaveBeenCalled();
  });
});
