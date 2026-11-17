/* eslint-disable react/prop-types */
// The API Key Configuration dialog names a card by its display name
// ("Hugging Face"), and its search finds the card by that name or by the
// provider id ("huggingface"); case, spaces, "_" and "-" do not count.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import {
  SERVER_PROVIDERS,
  providerStatusResponse,
} from "src/utils/__tests__/serverProviderList";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, delete: vi.fn() },
  endpoints: {
    develop: {
      apiKey: {
        status: "/model-hub/develops/provider-status/",
        delete: (id) => `/model-hub/api-keys/${id}/`,
      },
    },
    settings: {
      customModal: { deleteModel: "/model-hub/custom-models/delete/" },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
// Each card shows the name it is given; the card bodies are not under test.
vi.mock("src/sections/develop-detail/Common/ConfigureKeys/KeyCard", () => ({
  default: ({ data }) => (
    <div data-testid="provider-card">{data.display_name}</div>
  ),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import ConfigureKeys from "../ConfigureKeys";

function renderDialog() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfigureKeys open onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

const cardNames = () =>
  screen.queryAllByTestId("provider-card").map((card) => card.textContent);

describe("API Key Configuration dialog: search by card name or provider id", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockImplementation(async () => providerStatusResponse());
  });

  it.each([
    ["huggingface", "Hugging Face"],
    ["vertex_ai", "Google Vertex AI"],
    ["openai", "OpenAI"],
  ])("typing %s shows the %s card", async (search, name) => {
    renderDialog();
    const cards = await screen.findAllByTestId("provider-card");
    expect(cards).toHaveLength(SERVER_PROVIDERS.length);

    fireEvent.change(screen.getByPlaceholderText("Search"), {
      target: { value: search },
    });

    expect(cardNames()).not.toContain("Anthropic");
    expect(cardNames()).toContain(name);
  });
});
