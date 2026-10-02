import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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

  it("renders the provider logo from the API's snake_case `logo_url` field", async () => {
    mockModelList([
      { model_name: "gpt-5.5", providers: "openai", logo_url: OPENAI_LOGO },
    ]);
    renderSelector();

    // The model-list query is only enabled once the dropdown opens.
    await userEvent.click(screen.getByText("Select model"));

    expect(await screen.findByText("gpt-5.5")).toBeInTheDocument();
    const logo = document.querySelector(`img[src="${OPENAI_LOGO}"]`);
    expect(logo).toBeTruthy();
  });

  it("falls back to the cube icon (no <img>) when the model has no logo", async () => {
    mockModelList([{ model_name: "local-model", providers: "custom" }]);
    renderSelector();

    await userEvent.click(screen.getByText("Select model"));

    expect(await screen.findByText("local-model")).toBeInTheDocument();
    // No raster logo should render — the component uses an Iconify <svg> cube.
    expect(document.querySelector("img")).toBeNull();
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
