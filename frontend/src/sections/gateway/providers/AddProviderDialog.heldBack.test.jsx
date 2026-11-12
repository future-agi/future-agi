// Editing a provider whose saved models are all held back (its credential
// holds a value that is not text, so none can be offered): the dialog loads
// none, counts them, and still saves a replacement key. The request leaves the
// model list out, which keeps the saved IDs on the server.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";

vi.mock("./providerDiscovery", () => ({
  PROVIDER_MODEL_DISCOVERY_ENABLED: false,
}));
const { updateMutate, saved } = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  saved: { models: [], dropped: 0 },
}));
vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => false,
  useUpdateProvider: () => ({ mutate: updateMutate, isPending: false }),
  useFetchProviderModels: () => ({
    isPending: false,
    mutate: (vars, opts) => {
      if (vars.savedModelsOnly) {
        opts?.onSuccess?.({
          models: saved.models,
          dropped_model_count: saved.dropped,
        });
      }
    },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

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
const typeKey = (value) =>
  fireEvent.change(screen.getByLabelText(/^API Key/), { target: { value } });

beforeEach(() => {
  updateMutate.mockReset();
  saved.models = [];
  saved.dropped = 0;
});

describe("AddProviderDialog: editing a provider whose saved models are held back", () => {
  it("saves a replacement key and leaves the model list out", () => {
    saved.dropped = 2;
    renderEdit();
    expect(
      screen.getByText("2 model IDs were omitted for safety."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Saving is blocked/)).toBeNull();

    typeKey("fixture-replacement-key");
    expect(saveButton()).toBeEnabled();
    fireEvent.click(saveButton());

    expect(updateMutate).toHaveBeenCalledOnce();
    const { config } = updateMutate.mock.calls[0][0];
    expect(config.api_key).toBe("fixture-replacement-key");
    expect(config).not.toHaveProperty("models");
  });

  it("still asks for a model when no saved model is held back", () => {
    renderEdit();
    typeKey("fixture-replacement-key");
    expect(saveButton()).toBeDisabled();
  });

  it("sends the loaded models back when some were held back and some loaded", () => {
    saved.models = ["gpt-4o"];
    saved.dropped = 1;
    renderEdit();
    fireEvent.click(saveButton());

    expect(updateMutate).toHaveBeenCalledOnce();
    expect(updateMutate.mock.calls[0][0].config.models).toEqual(["gpt-4o"]);
  });
});
