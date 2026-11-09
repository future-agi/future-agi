import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import ModelSelector from "../ModelSelector";

const mockGet = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => mockGet(...args) },
  endpoints: {
    develop: {
      modelList: "/model-hub/api/models_list/",
      eval: {
        summaryTemplates: "/eval/summary-templates/",
        summaryTemplate: (id) => `/eval/summary-templates/${id}/`,
      },
    },
    falconAI: { connectors: "/falcon-ai/connectors/" },
    knowledge: { list: "/knowledge/list/" },
  },
}));

// KeysDrawer pulls in its own data fetching / drawer chrome that is
// irrelevant to the model-logo behaviour under test.
vi.mock("src/components/custom-model-dropdown/KeysDrawer", () => ({
  default: () => null,
}));

const OPENAI_LOGO =
  "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png";

function mockModelList(models) {
  mockGet.mockImplementation((url) => {
    if (url === "/model-hub/api/models_list/") {
      return Promise.resolve({
        data: { results: models, next: null, current_page: 1 },
      });
    }
    // connectors + knowledge bases — empty is fine for these tests
    return Promise.resolve({ data: { results: [] } });
  });
}

function renderSelector(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ModelSelector model="" onModelChange={vi.fn()} {...props} />
    </QueryClientProvider>,
  );
}

describe("ModelSelector — BYOK model logos", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("maps a realistic remote API logo to the local provider asset", async () => {
    mockModelList([
      { model_name: "gpt-5.5", providers: "openai", logo_url: OPENAI_LOGO },
    ]);
    renderSelector();

    // The model-list query is only enabled once the dropdown opens.
    await userEvent.click(screen.getByText("Select model"));

    expect(await screen.findByText("gpt-5.5")).toBeInTheDocument();
    const logo = document.querySelector(
      'img[src="/icons/settings/openai.png"]',
    );
    expect(logo).toBeTruthy();
  });

  it("uses a local generic provider image for an unknown provider", async () => {
    mockModelList([{ model_name: "local-model", providers: "custom" }]);
    renderSelector();

    await userEvent.click(screen.getByText("Select model"));

    expect(await screen.findByText("local-model")).toBeInTheDocument();
    expect(
      document.querySelector(
        'img[src="/assets/icons/settings/AIProviders.svg"]',
      ),
    ).toBeTruthy();
    expect(document.querySelector('img[src^="http"]')).toBeNull();
  });

  it("treats a model with snake_case `is_available: false` as unavailable (no selection on click)", async () => {
    mockModelList([
      { model_name: "gpt-5.5", providers: "openai", is_available: false },
    ]);
    const onModelChange = vi.fn();
    renderSelector({ onModelChange });

    await userEvent.click(screen.getByText("Select model"));
    const row = await screen.findByText("gpt-5.5");

    // Clicking an unavailable model opens the keys drawer instead of selecting it.
    await userEvent.click(row);
    expect(onModelChange).not.toHaveBeenCalled();
  });
});

// Connectors in the "+" menu are Falcon AI's MCP connectors, and their
// settings page is registered only where Falcon AI is allowed. The menu
// offers Connectors, and with it Manage connectors, only where the
// capabilities answer says falcon_ai is allowed; anywhere else, or while the
// answer is not in yet, the other options are offered without it.
describe("ModelSelector — Connectors where Falcon AI is allowed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.pushState({}, "", "/dashboard/evaluations");
  });

  function answerCapabilities(falconAI) {
    mockGet.mockImplementation((url) => {
      if (url === "/api/capabilities/") {
        if (falconAI === "pending") return new Promise(() => {});
        return Promise.resolve({
          data: {
            features: {
              falcon_ai: {
                allowed: falconAI === "allowed",
                reason_code: falconAI === "allowed" ? null : "LICENSE_REQUIRED",
              },
            },
          },
        });
      }
      return Promise.resolve({ data: { results: [] } });
    });
  }

  // The selector with the query client it reads capabilities through, so a
  // test can wait for the capabilities answer to be in.
  function renderWithClient() {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ModelSelector model="" onModelChange={vi.fn()} />
      </QueryClientProvider>,
    );
    return qc;
  }
  const capabilitiesStatus = (qc) =>
    qc.getQueryState(CAPABILITIES_QUERY_KEY)?.status;

  const openPlusMenu = async () => {
    await userEvent.click(screen.getByRole("button"));
    await screen.findByText("Knowledge Base");
  };

  it.each([
    ["denies Falcon AI", "denied"],
    ["has not answered yet", "pending"],
  ])(
    "offers no Connectors and no Manage connectors where the deployment %s",
    async (_where, falconAI) => {
      answerCapabilities(falconAI);
      const qc = renderWithClient();
      await openPlusMenu();
      if (falconAI === "denied") {
        await waitFor(() => expect(capabilitiesStatus(qc)).toBe("success"));
      } else {
        expect(capabilitiesStatus(qc)).toBe("pending");
      }
      expect(screen.getByText("Data Injection")).toBeInTheDocument();
      expect(screen.getByText("Summary")).toBeInTheDocument();
      expect(screen.queryByText("Connectors")).not.toBeInTheDocument();
      expect(
        screen.queryByText("Connect external tools to enhance evaluations."),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("Manage connectors")).not.toBeInTheDocument();
      expect(
        mockGet.mock.calls.some(([url]) => url === "/falcon-ai/connectors/"),
      ).toBe(false);
    },
  );

  it("offers Connectors and Manage connectors where Falcon AI is allowed", async () => {
    answerCapabilities("allowed");
    const qc = renderWithClient();
    await openPlusMenu();
    await waitFor(() => expect(capabilitiesStatus(qc)).toBe("success"));
    await userEvent.click(await screen.findByText("Connectors"));
    expect(
      await screen.findByText("No connectors configured"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByText("Manage connectors"));
    expect(window.location.pathname).toBe(
      "/dashboard/settings/falcon-ai-connectors",
    );
  });
});
