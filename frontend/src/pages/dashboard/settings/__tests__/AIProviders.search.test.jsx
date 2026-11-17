/* eslint-disable react/prop-types */
// Settings > AI Providers names a card by its display name ("Hugging Face"),
// and its search finds the card by that name or by the provider id
// ("huggingface"); case, spaces, "_" and "-" do not count.
import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import {
  SERVER_PROVIDERS,
  providerStatusResponse,
} from "src/utils/__tests__/serverProviderList";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

const STATUS_URL = "/model-hub/develops/provider-status/";

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
      customModal: {
        getCustomModal: "/model-hub/custom-models/",
        deleteModel: "/model-hub/custom-models/delete/",
      },
    },
  },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, trackEvent: vi.fn() }));
// Each card shows the name it is given; the card bodies are not under test.
vi.mock("src/sections/develop-detail/Common/ConfigureKeys/KeyCard", () => ({
  default: ({ data }) => (
    <div data-testid="provider-card">{data.display_name}</div>
  ),
}));
vi.mock("src/components/custom-model-dropdown/CloudProviderCard", () => ({
  default: ({ provider }) => (
    <div data-testid="provider-card">{provider.display_name}</div>
  ),
}));
vi.mock("src/components/custom-model-dropdown/CustomModalKeyCard", () => ({
  default: () => null,
}));
vi.mock("src/pages/dashboard/settings/AddCustomModal", () => ({
  default: () => null,
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));

import AIProviders from "../AIProviders";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <HelmetProvider>
      <QueryClientProvider client={queryClient}>
        <AIProviders />
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const cardNames = () =>
  screen.queryAllByTestId("provider-card").map((card) => card.textContent);

describe("Settings > AI Providers: search by card name or provider id", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockImplementation(async (url) => {
      if (url === STATUS_URL) return providerStatusResponse();
      return { data: { results: [] } };
    });
  });

  it.each([
    ["huggingface", "Hugging Face"],
    ["vertex_ai", "Google Vertex AI"],
    ["openai", "OpenAI"],
  ])("typing %s shows the %s card", async (search, name) => {
    renderPage();
    const cards = await screen.findAllByTestId("provider-card");
    expect(cards).toHaveLength(SERVER_PROVIDERS.length);

    fireEvent.change(screen.getByPlaceholderText("Search AI Provider"), {
      target: { value: search },
    });

    // The search applies after its debounce; Anthropic matches none of these.
    await waitFor(() => expect(cardNames()).not.toContain("Anthropic"));
    expect(cardNames()).toContain(name);
  });
});
