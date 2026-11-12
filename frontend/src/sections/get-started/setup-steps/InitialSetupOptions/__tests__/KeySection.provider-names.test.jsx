/* eslint-disable react/prop-types */
// Get started > Add Keys: the provider cards read "Openai" and "Palm". The
// server's provider list title-cases provider ids; the cards name each
// provider the way the product spells it, and every card stays.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import KeySection from "../KeySection";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

const STATUS_URL = "/model-hub/develops/provider-status/";
const CUSTOM_MODELS_URL = "/model-hub/custom-models/";

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
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
// The credits route above the cards is covered by
// get-started-credits-route.test.jsx; here the credits status stays loading.
vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => ({ data: undefined, isLoading: true }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15, currentOrganizationId: "org-1" }),
}));

// GET /model-hub/develops/provider-status/ as the server sends it: each
// display_name is the provider id title-cased.
const SERVER_PROVIDERS = [
  { provider: "openai", display_name: "Openai", type: "text" },
  { provider: "anthropic", display_name: "Anthropic", type: "text" },
  { provider: "palm", display_name: "Palm", type: "text" },
  { provider: "openrouter", display_name: "Openrouter", type: "text" },
  { provider: "azure", display_name: "Azure", type: "json" },
];

const RESPONSES = {
  [STATUS_URL]: { data: { result: { providers: SERVER_PROVIDERS } } },
  [CUSTOM_MODELS_URL]: { data: { results: [] } },
};

function renderKeySection() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <KeySection setCurrentLabel={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("Get started, Add keys step: provider card names", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockImplementation((url) =>
      Promise.resolve(RESPONSES[url] ?? { data: {} }),
    );
  });

  it("spells OpenAI and PaLM as brands and keeps every card", async () => {
    renderKeySection();

    await waitFor(() =>
      expect(screen.getAllByTestId("provider-card")).toHaveLength(5),
    );
    const cards = screen.getAllByTestId("provider-card");

    expect(cards.map((card) => card.textContent)).toEqual([
      "OpenAI",
      "Anthropic",
      "PaLM",
      "OpenRouter",
      "Azure OpenAI",
    ]);
    expect(screen.queryByText("Openai")).toBeNull();
    expect(screen.queryByText("Palm")).toBeNull();
  });
});
