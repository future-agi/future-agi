import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
} from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AddProviderDialog from "./AddProviderDialog";
import {
  DECISIONS_ONLY_TAG,
  decisionModelsFor,
  defaultSelectableModels,
  withDecisionModels,
} from "./modelCompatibility";

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
      <AddProviderDialog
        open
        onClose={vi.fn()}
        gatewayId="gw-1"
        provider={provider}
      />
    </QueryClientProvider>,
  );

const pickProvider = async (label) => {
  await userEvent.click(screen.getByRole("combobox", { name: /^Provider/ }));
  await userEvent.click(screen.getByRole("option", { name: label }));
};

const setModel = async (model) => {
  await userEvent.type(
    screen.getByPlaceholderText(/comma-separated model IDs|type manually/i),
    `${model}{enter}`,
  );
};

describe("OpenRouter provider configuration", () => {
  it("offers the OpenRouter chat preset and saves its default format", async () => {
    fetchMutate.mockImplementation((_vars, opts) =>
      setTimeout(() => opts.onSuccess?.({ models: ["openai/gpt-4o"] }), 0),
    );
    renderDialog();
    await pickProvider("OpenRouter");
    expect(screen.getByLabelText(/^Base URL/).value).toBe(
      "https://openrouter.ai/api/v1",
    );
    expect(
      screen.getByRole("combobox", { name: /^API Format/ }).textContent,
    ).toBe("openai");
    await setModel("openai/gpt-4o");
    await userEvent.type(screen.getByLabelText(/^API Key/), "synthetic-key");
    await waitFor(() => expect(fetchMutate).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Add Provider" }));
    expect(updateMutate.mock.calls.at(-1)[0]).toMatchObject({
      name: "openrouter",
      config: {
        base_url: "https://openrouter.ai/api/v1",
        api_format: "openai",
      },
    });
  });

  it("offers OpenRouter only on its chat format, which serves decisions too", async () => {
    fetchMutate.mockClear();
    fetchMutate.mockImplementation((_vars, opts) =>
      setTimeout(() => opts.onSuccess?.({ models: ["openai/gpt-4o"] }), 0),
    );
    renderDialog();
    await pickProvider("OpenRouter");
    const format = screen.getByRole("combobox", { name: /^API Format/ });
    // System One with the /api/v1 base URL would post to
    // /api/v1/api/alpha/decisions, so the preset does not offer it.
    expect(format.textContent).toBe("openai");
    expect(format.getAttribute("aria-disabled")).toBe("true");
    await setModel("openai/gpt-4o");
    await userEvent.type(screen.getByLabelText(/^API Key/), "synthetic-key");
    await waitFor(() => expect(fetchMutate).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "Add Provider" }));
    expect(updateMutate.mock.calls.at(-1)[0].config).toMatchObject({
      base_url: "https://openrouter.ai/api/v1",
      api_format: "openai",
    });
  });

  it("starts OpenRouter on its chat format after a System One preset", async () => {
    renderDialog();
    await pickProvider("TypeSafe");
    expect(
      screen.getByRole("combobox", { name: /^API Format/ }).textContent,
    ).toMatch(/System One/);
    await pickProvider("OpenRouter");
    expect(
      screen.getByRole("combobox", { name: /^API Format/ }).textContent,
    ).toBe("openai");
  });

  it("edits a saved systemone OpenRouter provider without probing it", async () => {
    fetchMutate.mockClear();
    renderDialog({
      name: "openrouter",
      config: {
        base_url: "https://openrouter.ai",
        api_format: "systemone",
        models: ["typesafe/jev-1.13"],
      },
    });
    expect(
      screen.getByRole("combobox", { name: /^API Format/ }).textContent,
    ).toBe("System One (decision models)");
    expect(fetchMutate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(updateMutate.mock.calls.at(-1)[0].config.api_format).toBe(
      "systemone",
    );
  });

  it("does not advertise keyless editing for a System One OpenRouter", () => {
    // A saved OpenRouter provider cannot be made keyless by changing its URL.
    renderDialog({
      name: "openrouter",
      config: {
        api_format: "systemone",
        base_url: "https://decider.example.com",
        models: ["typesafe/jev-1.13"],
      },
    });
    expect(screen.queryByText(/Optional for a self-hosted/)).toBeNull();
  });

  it("requires a key for the OpenRouter host even under another name", async () => {
    renderDialog();
    await pickProvider("Custom System One endpoint");
    fireEvent.change(screen.getByLabelText(/^Base URL/), {
      target: { value: "https://API.OpenRouter.AI./api/v1" },
    });
    expect(screen.getByLabelText(/^API Key/).required).toBe(true);
  });

  it("describes the generic endpoint without promising the wrong vendor path", async () => {
    renderDialog();
    await pickProvider("Custom System One endpoint");
    expect(
      screen.queryByText(/gateway posts to this URL plus \/v1\/systemone/),
    ).toBeNull();
  });

  it("offers Jev decisions beside the live listing but not in Select All", () => {
    const context = { providerName: "openrouter", apiFormat: "openai" };
    const decisionModels = [
      "typesafe/jev-1.13",
      "typesafe/jev-1.14",
      "~typesafe/jev-latest",
    ];
    expect(decisionModelsFor(context)).toEqual(decisionModels);
    expect(
      decisionModelsFor({
        baseUrl: "https://openrouter.ai/api/v1",
        apiFormat: "openai",
      }),
    ).toEqual(decisionModels);
    expect(withDecisionModels(["openai/gpt-4o"], context)).toEqual([
      "openai/gpt-4o",
      ...decisionModels,
    ]);
    expect(
      defaultSelectableModels(["openai/gpt-4o", ...decisionModels], context),
    ).toEqual(["openai/gpt-4o"]);
    expect(withDecisionModels([], context)).toEqual([]);
    expect(decisionModelsFor({ ...context, apiFormat: "systemone" })).toEqual(
      [],
    );
    expect(DECISIONS_ONLY_TAG).toBe("Decisions API only");
  });
});
