// Today's behaviour, pinned as a known limit: with model discovery on, a
// provider whose saved models are all held back can save a replacement key
// without a model list, but only when the typed key lists models. When the
// typed key's model fetch fails, Save stays disabled until a model ID is
// added by hand, and that model list then replaces the saved one.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";

vi.mock("./providerDiscovery", () => ({
  PROVIDER_MODEL_DISCOVERY_ENABLED: true,
}));
const { updateMutate, fetchMutate } = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  fetchMutate: vi.fn(),
}));
vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => false,
  useUpdateProvider: () => ({ mutate: updateMutate, isPending: false }),
  useFetchProviderModels: () => ({
    isPending: false,
    mutate: (vars, opts) => {
      // Every saved model is held back: the load lists none and counts them.
      if (vars.savedModelsOnly) {
        opts?.onSuccess?.({ models: [], dropped_model_count: 2 });
        return;
      }
      fetchMutate(vars, opts);
    },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const DEBOUNCED_WAIT = 4000;
const KEY_FETCH_FAILED =
  "Couldn't load any models with this key. Check that it is valid for this " +
  "provider, or add model IDs manually below.";

const later =
  (impl) =>
  (...args) =>
    setTimeout(() => impl(...args), 0);

const renderEdit = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddProviderDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        provider={{
          name: "openai",
          config: { base_url: "https://api.openai.com" },
        }}
      />
    </QueryClientProvider>,
  );
const saveButton = () => screen.getByRole("button", { name: "Save Changes" });

beforeEach(() => {
  updateMutate.mockReset();
  fetchMutate.mockReset();
  fetchMutate.mockImplementation(
    later((vars, opts) => {
      if (!vars.apiKey) {
        // The saved credential is not text: listing with it is refused.
        opts?.onError?.(new Error("Provider credential context is invalid."));
        return;
      }
      opts?.onSuccess?.({
        models: [],
        error: "Provider rejected the credential.",
        code: "provider_authentication_failed",
      });
    }),
  );
});

describe("AddProviderDialog with discovery on: every saved model held back", () => {
  it("today: when the typed key's model fetch fails, Save stays disabled until a model is added by hand", async () => {
    renderEdit();
    expect(
      screen.getByText("2 model IDs were omitted for safety."),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^API Key/), {
      target: { value: "fixture-typed-key" },
    });
    expect(
      (
        await screen.findAllByText(
          KEY_FETCH_FAILED,
          {},
          { timeout: DEBOUNCED_WAIT },
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(saveButton()).toBeDisabled();
    fireEvent.click(saveButton());
    expect(updateMutate).not.toHaveBeenCalled();

    await userEvent.type(
      screen.getByLabelText("Model ID"),
      "manual-model{enter}",
    );
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());

    expect(updateMutate).toHaveBeenCalledOnce();
    const { config } = updateMutate.mock.calls[0][0];
    expect(config.api_key).toBe("fixture-typed-key");
    // The hand-added list is sent, so it replaces the saved IDs.
    expect(config.models).toEqual(["manual-model"]);
  });
});
