import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ModelSelector from "../ModelSelector";

const mockGet = vi.fn();
let capabilityResponse;

vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeEach(() => {
  capabilityResponse = Promise.resolve({
    data: {
      features: {
        turing_models: { allowed: true },
        jev_models: { allowed: true },
      },
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network request");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

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
    if (url === "/api/capabilities/") return capabilityResponse;
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

describe("ModelSelector — Jev managed models", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockModelList([
      { model_name: "jev-1.13.0" },
      { model_name: "jev-latest" },
      { model_name: "local-model", providers: "custom" },
    ]);
  });

  it("orders Turing, Jev pinned/latest, then Your Models without duplicate Jev rows", async () => {
    renderSelector();
    await userEvent.click(screen.getByText("Select model"));
    await screen.findByText("local-model");
    const rows = screen.getAllByRole("menuitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Turing LargeBest accuracy for complex evaluations",
      "Turing SmallBalanced accuracy, lower cost",
      "Turing FlashFast, low-latency evaluations",
      "Jev 1.13 (pinned)jev-1.13.0",
      "Jev latestjev-latest",
      "local-modelcustom",
    ]);
    const headers = ["FutureAGI Models", "Jev models", "Your Models"].map(
      (text) => screen.getByText(text),
    );
    expect(
      headers[0].compareDocumentPosition(headers[1]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      headers[1].compareDocumentPosition(headers[2]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it.each(["loading", "denied"])(
    "locks Jev while %s and retains a preselected value",
    async (state) => {
      capabilityResponse =
        state === "loading"
          ? new Promise(() => {})
          : Promise.resolve({
              data: { features: { jev_models: { allowed: false } } },
            });
      const onModelChange = vi.fn();
      renderSelector({ model: "jev-1.13.0", onModelChange });
      await userEvent.click(screen.getByText("Jev 1.13 (pinned)"));
      const row = screen.getByRole("menuitem", { name: /Jev 1.13/ });
      expect(row).toHaveAttribute("aria-disabled", "true");
      expect(row).toHaveClass("Mui-selected");
      await userEvent.hover(row.parentElement);
      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        "Not included in your plan",
      );
      expect(onModelChange).not.toHaveBeenCalled();
    },
  );

  it.each(["pinned", "jev-1.13.0", "jev-1"])(
    "searches label or provider ID with %s",
    async (query) => {
      renderSelector();
      await userEvent.click(screen.getByText("Select model"));
      await userEvent.type(
        screen.getByPlaceholderText("Search models..."),
        query,
      );
      expect(screen.getByText("Jev 1.13 (pinned)")).toBeInTheDocument();
      expect(screen.queryByText("Jev latest")).not.toBeInTheDocument();
      expect(screen.queryByText("FutureAGI Models")).not.toBeInTheDocument();
    },
  );

  it("selects the exact provider ID and labels only exact Jev values", async () => {
    const onModelChange = vi.fn();
    renderSelector({ model: "latest", onModelChange });
    await userEvent.click(screen.getByText("latest"));
    const row = screen.getByRole("menuitem", { name: /Jev latest/ });
    await waitFor(() => expect(row).not.toHaveAttribute("aria-disabled"));
    await userEvent.click(row);
    expect(onModelChange).toHaveBeenCalledWith("jev-latest");
  });
});
