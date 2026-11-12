import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Exercise discovery-ON behavior; launch-OFF has a separate unmocked flag suite.
vi.mock("./providerDiscovery", () => ({ PROVIDER_MODEL_DISCOVERY_ENABLED: true }));
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";
import { enqueueSnackbar } from "notistack";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const { fetchMutate, updateMutate, projection, updateState } = vi.hoisted(
  () => ({
    fetchMutate: vi.fn(),
    updateMutate: vi.fn(),
    projection: { models: ["gpt-4o"], dropped_model_count: 0 },
    updateState: { error: null },
  }),
);
vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => false,
  useUpdateProvider: () => ({
    mutate: updateMutate,
    isPending: false,
    ...updateState,
  }),
  useFetchProviderModels: () => ({
    mutate: (vars, callbacks) =>
      vars.savedModelsOnly
        ? setTimeout(() => callbacks.onSuccess({ ...projection }), 0)
        : fetchMutate(vars, callbacks),
    isPending: false,
  }),
}));

// The server owns admission. Mocks represent its filtered response, not raw
// upstream IDs; the HTTP suite separately exercises the real hook and request.
const unsafe = [
  "cataloguesk-test-not-a-real-provider-key",
  "aB3dE6gH9jK2mN5p/Q8sT1vW4yZ7cF0iL",
  "original-fixture-only",
  "b3JpZ2luYWwtZml4dHVyZS1vbmx5",
];
const renderSaved = (models = ["gpt-4o"], onClose = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddProviderDialog
        open
        onClose={onClose}
        gatewayId="synthetic-gateway"
        provider={{ name: "openai", config: { models, default_timeout: 30 } }}
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  fetchMutate.mockReset();
  updateMutate.mockReset();
  enqueueSnackbar.mockReset();
  updateState.error = null;
  projection.models = ["gpt-4o"];
  projection.dropped_model_count = 0;
});

describe("server-admitted model lists", () => {
  it("preserves saved IDs outside discovery grammar and explicit manual IDs", async () => {
    const savedId = "NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO";
    const manualId = "ft:gpt-3.5-turbo-0125:personal::8sNGh8Xd";
    projection.models = [savedId];
    fetchMutate.mockImplementation((_vars, callbacks) => {
      setTimeout(() => callbacks.onSuccess({ models: ["gpt-4o"] }), 0);
    });
    renderSaved([savedId]);
    await screen.findByText(savedId);
    await userEvent.type(
      screen.getByRole("combobox", { name: "" }),
      `${manualId}{enter}`,
    );
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config.models).toEqual([
      savedId,
      manualId,
    ]);
  });

  it.each([0, 2])(
    "shows save-time omissions without silently closing: %i",
    async (count) => {
      const onClose = vi.fn();
      fetchMutate.mockImplementation((_vars, callbacks) => {
        setTimeout(() => callbacks.onSuccess({ models: ["gpt-4o"] }), 0);
      });
      updateMutate.mockImplementation((_vars, callbacks) => {
        callbacks.onSuccess({
          gateway_synced: true,
          dropped_model_count: count,
        });
      });
      renderSaved(["gpt-4o"], onClose);
      await screen.findByText("(1 available)");
      await userEvent.click(
        screen.getByRole("button", { name: "Save Changes" }),
      );
      if (count) {
        expect(
          await screen.findByText(/Saved with 2 model IDs omitted for safety/),
        ).toBeTruthy();
        expect(onClose).not.toHaveBeenCalled();
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          'Provider "openai" updated. 2 model IDs were omitted for safety.',
          { variant: "warning" },
        );
      } else {
        expect(onClose).toHaveBeenCalledOnce();
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          'Provider "openai" updated',
          { variant: "success" },
        );
      }
    },
  );

  it("shows omission count even when the saved write did not sync", async () => {
    updateState.error = {
      savedButNotApplied: true,
      message: "Saved, but the gateway did not apply it.",
      response: { dropped_model_count: 3 },
    };
    renderSaved();
    expect(
      await screen.findByText(/Saved with 3 model IDs omitted for safety/),
    ).toBeTruthy();
  });
  it("never hydrates legacy config IDs and shows only server omission counts", async () => {
    projection.dropped_model_count = unsafe.length;
    fetchMutate.mockImplementation((_vars, callbacks) => {
      setTimeout(
        () =>
          callbacks.onSuccess({
            models: ["gpt-4o", "gpt-4o-mini"],
            dropped_model_count: 2,
          }),
        0,
      );
    });
    renderSaved([...unsafe, "gpt-4o"]);
    for (const value of unsafe)
      expect(document.body.textContent).not.toContain(value);
    expect(
      await screen.findByText("6 model IDs were omitted for safety."),
    ).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Select All" }));
    for (const value of unsafe)
      expect(document.body.textContent).not.toContain(value);
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config.models).toEqual([
      "gpt-4o",
      "gpt-4o-mini",
    ]);
  });

  it("does not blame a valid saved key when the current list is entirely omitted", async () => {
    fetchMutate.mockImplementation((_vars, callbacks) => {
      setTimeout(
        () => callbacks.onSuccess({ models: [], dropped_model_count: 4 }),
        0,
      );
    });
    renderSaved();
    expect(
      await screen.findByText("4 model IDs were omitted for safety."),
    ).toBeTruthy();
    expect(
      screen.queryByText(/check that it is valid for this provider/i),
    ).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config.models).toEqual(["gpt-4o"]);
  });

  it.each([0, 3])(
    "preserves definitive authentication refusal with historical and current omissions=%i",
    async (count) => {
      projection.dropped_model_count = 1;
      fetchMutate.mockImplementation((vars, callbacks) => {
        setTimeout(
          () =>
            callbacks.onSuccess(
              vars.apiKey
                ? {
                    models: [],
                    code: "provider_authentication_failed",
                    error: "Authentication failed.",
                    dropped_model_count: count,
                  }
                : { models: ["gpt-4o"] },
            ),
          0,
        );
      });
      renderSaved(["sk-test-not-a-real-provider-key", "gpt-4o"]);
      await screen.findByText("1 model IDs were omitted for safety.");
      await userEvent.type(
        screen.getByLabelText(/^API Key/i),
        "replacement-fixture-only",
      );
      await waitFor(
        () => expect(fetchMutate.mock.calls.some(([v]) => v.apiKey)).toBe(true),
        { timeout: 4000 },
      );
      await screen.findAllByText(/Couldn't load any models with this key/);
      expect(
        screen.getByRole("button", { name: "Save Changes" }).disabled,
      ).toBe(true);
      fireEvent.click(
        screen.getByRole("button", { name: "Save Changes" }),
      );
      expect(updateMutate).not.toHaveBeenCalled();
      await userEvent.type(
        screen.getByRole("combobox", { name: "" }),
        "org/model:tag{enter}",
      );
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: "Save Changes" }).disabled,
        ).toBe(false),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Save Changes" }),
      );
      expect(updateMutate.mock.calls[0][0].config.models).toContain(
        "org/model:tag",
      );
    },
  );
});
