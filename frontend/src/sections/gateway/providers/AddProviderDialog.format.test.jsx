import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, userEvent, waitFor } from "src/utils/test-utils";
import AddProviderDialog from "./AddProviderDialog";

const { updateMutate, fetchMutate } = vi.hoisted(() => ({
  updateMutate: vi.fn(),
  fetchMutate: vi.fn(),
}));

vi.mock("./hooks/useGatewayConfig", () => ({
  gatewayConfigRefreshFailed: () => false,
  useUpdateProvider: () => ({ mutate: updateMutate, isPending: false }),
  useFetchProviderModels: () => ({ mutate: fetchMutate, isPending: false }),
}));

const renderDialog = (provider) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AddProviderDialog open onClose={vi.fn()} gatewayId="gw-1" provider={provider} />
    </QueryClientProvider>,
  );

const formatField = () => screen.getByRole("combobox", { name: /^API Format/ });
const pickProvider = async (label) => {
  await userEvent.click(screen.getByRole("combobox", { name: /^Provider/ }));
  await userEvent.click(screen.getByRole("option", { name: label, exact: true }));
};

beforeEach(() => {
  updateMutate.mockReset();
  fetchMutate.mockReset();
  fetchMutate.mockImplementation((_vars, opts) => {
    setTimeout(() => opts.onSuccess({ models: ["synthetic-model"] }), 0);
  });
});

// Extends the contributor's #2281 transitions to the discovery and save payloads.
describe("provider format transitions", () => {
  it.each([
    ["Anthropic", "anthropic", "anthropic"],
    ["Google (Gemini)", "google", "google"],
    ["Azure OpenAI", "azure", "azure"],
    ["OpenAI", "openai", "openai"],
  ])("selects, discovers and saves the native adapter for %s", async (label, name, format) => {
    renderDialog();
    if (name === "openai") await pickProvider("Anthropic");
    await pickProvider(label);
    expect(formatField()).toHaveTextContent(format);
    expect(formatField()).toHaveAttribute("aria-disabled", "true");

    fireEvent.change(screen.getByLabelText(/^Base URL/), {
      target: { value: "https://provider.example.test" },
    });
    fireEvent.change(screen.getByLabelText(/^API Key/), {
      target: { value: "synthetic-provider-key" },
    });
    await waitFor(() => expect(fetchMutate).toHaveBeenCalledWith(
      expect.objectContaining({ apiFormat: format }), expect.any(Object),
    ));
    await waitFor(() => expect(screen.getByRole("button", { name: "Add Provider" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Add Provider" }));
    expect(updateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ name, config: expect.objectContaining({ api_format: format }) }),
      expect.any(Object),
    );
  });

  it("retains a supported format on Custom and permits explicit selection", async () => {
    renderDialog();
    await pickProvider("Anthropic");
    await pickProvider("Custom / Self-hosted");
    expect(formatField()).toHaveTextContent("anthropic");
    expect(formatField()).not.toHaveAttribute("aria-disabled", "true");
    await userEvent.click(formatField());
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "openai", "anthropic", "google",
    ]);
    await userEvent.click(screen.getByRole("option", { name: "google", exact: true }));
    expect(formatField()).toHaveTextContent("google");
    await pickProvider("Anthropic");
    expect(formatField()).toHaveTextContent("anthropic");
  });

  it("falls back to Custom's default when the previous format is unsupported", async () => {
    renderDialog();
    await pickProvider("Azure OpenAI");
    expect(formatField()).toHaveTextContent("azure");
    await pickProvider("Custom / Self-hosted");
    expect(formatField()).toHaveTextContent("openai");
  });

  it("does not silently rewrite an existing Google OpenAI-compatible connection", async () => {
    renderDialog({ name: "google", config: {
      base_url: "https://provider.example.test/v1beta/openai",
      api_format: "openai",
      models: ["synthetic-model"],
    } });
    expect(formatField()).toHaveTextContent("openai");
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls[0][0].config.api_format).toBe("openai");
  });

  it("retains explicit format selection for a saved custom name", async () => {
    renderDialog({ name: "my-proxy", config: {
      base_url: "https://provider.example.test",
      api_format: "anthropic",
      models: ["synthetic-model"],
    } });
    expect(formatField()).toHaveTextContent("anthropic");
    expect(formatField()).not.toHaveAttribute("aria-disabled", "true");
  });
});
