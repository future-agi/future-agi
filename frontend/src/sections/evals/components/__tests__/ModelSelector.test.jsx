/* eslint-disable react/prop-types */
import React, { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import ModelSelector from "../ModelSelector";

const mockGet = vi.fn();
const keysDrawer = vi.fn();
let capabilityResponse;
let typesafeHasKey;

vi.mock("src/components/iconify", () => ({ default: () => null }));

beforeEach(() => {
  typesafeHasKey = true;
  capabilityResponse = Promise.resolve({
    data: {
      features: {
        turing_models: { allowed: true },
        jev_models: { allowed: true },
        falcon_ai: { allowed: true },
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
      apiKey: { status: "/model-hub/develops/provider-status/" },
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
  default: (props) => {
    keysDrawer(props);
    return null;
  },
}));

const OPENAI_LOGO =
  "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png";

function mockModelList(models) {
  mockGet.mockImplementation((url, { params } = {}) => {
    if (url === "/api/capabilities/") return capabilityResponse;
    if (url === "/model-hub/develops/provider-status/") {
      return Promise.resolve({
        data: {
          result: {
            providers: [{ provider: "typesafe", has_key: typesafeHasKey }],
          },
        },
      });
    }
    if (url === "/model-hub/api/models_list/") {
      return Promise.resolve({
        data: {
          results: models.filter((m) =>
            m.model_name
              .toLowerCase()
              .includes(params?.search?.toLowerCase() || ""),
          ),
          next: null,
          current_page: 1,
        },
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
    await userEvent.click(screen.getByRole("button", { name: "Configure evaluation" }));
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

describe("ModelSelector — Jev models (bring your own key)", () => {
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

  it("locks Jev on a confirmed denial and retains a preselected value", async () => {
    capabilityResponse = Promise.resolve({
      data: { features: { jev_models: { allowed: false } } },
    });
    const onModelChange = vi.fn();
    renderSelector({ model: "jev-1.13.0", onModelChange });
    await userEvent.click(screen.getByText("Jev 1.13 (pinned)"));
    const row = await screen.findByRole("menuitem", { name: /Jev 1.13/ });
    await waitFor(() => expect(row).toHaveAttribute("aria-disabled", "true"));
    expect(row).toHaveClass("Mui-selected");
    await userEvent.hover(row.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Not included in your plan",
    );
    expect(onModelChange).not.toHaveBeenCalled();
  });

  // Self-hosted always allows Jev, and the server checks every run: a
  // capabilities fetch still loading, or one that failed, never locks it.
  it.each(["loading", "failed"])(
    "leaves Jev open while capabilities are %s",
    async (state) => {
      capabilityResponse =
        state === "loading"
          ? new Promise(() => {})
          : Promise.reject(new Error("capabilities unavailable"));
      renderSelector({ model: "jev-1.13.0" });
      await userEvent.click(screen.getByText("Jev 1.13 (pinned)"));
      const row = screen.getByRole("menuitem", { name: /Jev 1.13/ });
      expect(row).not.toHaveAttribute("aria-disabled", "true");
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

  it("asks for the org's TypeSafe key instead of selecting Jev without one", async () => {
    typesafeHasKey = false;
    const onModelChange = vi.fn();
    renderSelector({ onModelChange });
    await userEvent.click(screen.getByText("Select model"));
    const row = await screen.findByRole("menuitem", {
      name: /Jev latest.*Add your TypeSafe API key/,
    });
    await userEvent.click(row);
    expect(onModelChange).not.toHaveBeenCalled();
    // The picker closes so its backdrop can't swallow clicks in the drawer.
    await waitFor(() =>
      expect(
        screen.queryByRole("menuitem", { name: /Jev latest/ }),
      ).not.toBeInTheDocument(),
    );
    expect(keysDrawer).toHaveBeenLastCalledWith(
      expect.objectContaining({
        open: true,
        selectedModel: { model_name: "jev-latest", providers: "typesafe" },
      }),
    );
  });

  // Jev is in Community: an open-source server allows it (Turing stays
  // locked), and without a key it asks for one, as on Cloud.
  it("offers Jev on a self-hosted open-source server", async () => {
    capabilityResponse = Promise.resolve({
      data: {
        deployment_flavor: "oss",
        features: {
          turing_models: { allowed: false, reason_code: "OSS_LOCKED" },
          jev_models: { allowed: true },
        },
      },
    });
    typesafeHasKey = false;
    const onModelChange = vi.fn();
    renderSelector({ onModelChange });
    await userEvent.click(screen.getByText("Select model"));
    const row = await screen.findByRole("menuitem", {
      name: /Jev latest.*Add your TypeSafe API key/,
    });
    await waitFor(() => expect(row).not.toHaveAttribute("aria-disabled"));
    expect(
      screen.getByRole("menuitem", { name: /Turing Large/ }),
    ).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(row);
    expect(onModelChange).not.toHaveBeenCalled();
    expect(keysDrawer).toHaveBeenLastCalledWith(
      expect.objectContaining({
        open: true,
        selectedModel: { model_name: "jev-latest", providers: "typesafe" },
      }),
    );
  });

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

function StatefulSelector({
  initialMode = "agent",
  controlled = true,
  ...props
}) {
  const [model, setModel] = useState("local-model");
  const [mode, setMode] = useState(initialMode);
  return (
    <>
      <output data-testid="parent-mode">{mode}</output>
      <ModelSelector
        {...props}
        model={model}
        onModelChange={setModel}
        mode={controlled ? mode : undefined}
        onModeChange={controlled ? setMode : undefined}
      />
    </>
  );
}

function renderStatefulSelector(props = {}) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <StatefulSelector {...props} />
    </QueryClientProvider>,
  );
}

async function chooseModel(label) {
  await userEvent.click(
    screen.getByRole("button", { name: /^Evaluation model:/ }),
  );
  const row = await screen.findByRole("menuitem", { name: label });
  await waitFor(() => expect(row).not.toHaveAttribute("aria-disabled", "true"));
  await userEvent.click(row);
}

// jsdom can't match :focus-visible, so read the rule emotion wrote for the
// element's own class.
function focusRing(element) {
  const rules = Array.from(document.styleSheets).flatMap((sheet) =>
    Array.from(sheet.cssRules, (rule) => rule.cssText),
  );
  return rules.find((rule) =>
    Array.from(element.classList).some((name) =>
      rule.startsWith(`.${name}:focus-visible`),
    ),
  );
}

// The transition the element's own rule ends with: emotion writes a Button's
// theme styles and its sx into one class.
function transitionOf(element) {
  return Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from(sheet.cssRules))
    .filter((rule) =>
      Array.from(element.classList).some(
        (name) => rule.selectorText === `.${name}`,
      ),
    )
    .map((rule) => rule.style.transition)
    .filter(Boolean)
    .pop();
}

describe("ModelSelector Jev transitions and accessible controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockModelList([{ model_name: "local-model", providers: "custom" }]);
  });

  it.each(["auto", "agent"])(
    "switches controlled %s to Quick and restores it when leaving Jev",
    async (initialMode) => {
      renderStatefulSelector({ initialMode });
      await chooseModel(/Jev latest/);
      expect(screen.getByTestId("parent-mode")).toHaveTextContent("quick");
      await userEvent.click(
        screen.getByRole("button", { name: "Evaluation mode: Quick" }),
      );
      for (const mode of ["Auto", "Agent"]) {
        const row = screen.getByRole("menuitem", {
          name: new RegExp(`^${mode}`),
        });
        expect(row).toHaveAttribute("aria-disabled", "true");
        expect(row).toHaveTextContent("Jev runs in Quick mode only");
      }
      await userEvent.click(screen.getByRole("menuitem", { name: /^Quick/ }));
      await chooseModel(/Jev 1.13/);
      await chooseModel(/local-model/);
      expect(screen.getByTestId("parent-mode")).toHaveTextContent(initialMode);
      expect(
        screen.getByRole("button", {
          name: `Evaluation mode: ${initialMode === "auto" ? "Auto" : "Agent"}`,
        }),
      ).toBeInTheDocument();
    },
  );

  it("restores a locally managed mode after leaving Jev", async () => {
    renderStatefulSelector({ controlled: false });
    await chooseModel(/Jev latest/);
    expect(
      screen.getByRole("button", { name: "Evaluation mode: Quick" }),
    ).toBeInTheDocument();
    await chooseModel(/local-model/);
    expect(
      screen.getByRole("button", { name: "Evaluation mode: Agent" }),
    ).toBeInTheDocument();
  });

  it("disables unsupported configuration without deleting it and does not offer Summary", async () => {
    const onUseInternetChange = vi.fn();
    const onActiveSummaryChange = vi.fn();
    const onActiveConnectorIdsChange = vi.fn();
    const onSelectedKBsChange = vi.fn();
    const onActiveContextOptionsChange = vi.fn();
    renderStatefulSelector({
      useInternet: true,
      activeSummary: "long",
      activeConnectorIds: ["connector"],
      selectedKBs: ["kb"],
      activeContextOptions: ["trace_context"],
      onUseInternetChange,
      onActiveSummaryChange,
      onActiveConnectorIdsChange,
      onSelectedKBsChange,
      onActiveContextOptionsChange,
    });
    await chooseModel(/Jev latest/);
    await userEvent.click(
      screen.getByRole("button", { name: "Configure evaluation" }),
    );
    for (const name of [
      /^Use Internet/,
      /^Connectors/,
      /^Knowledge Base/,
      /^Data Injection/,
    ]) {
      const row = screen.getByRole("menuitem", { name });
      expect(row).toHaveAttribute("aria-disabled", "true");
      expect(row).toHaveTextContent("Jev does not support");
      expect(row).toHaveTextContent("preserved");
    }
    expect(
      screen.queryByRole("menuitem", { name: /^Summary/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Long")).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await chooseModel(/local-model/);
    expect(screen.getByText("Internet")).toBeInTheDocument();
    expect(screen.getByText("Long")).toBeInTheDocument();
    expect(screen.getByText("1 KB")).toBeInTheDocument();
    expect(screen.getByText("+1 context")).toBeInTheDocument();
    for (const cb of [
      onUseInternetChange,
      onActiveSummaryChange,
      onActiveConnectorIdsChange,
      onSelectedKBsChange,
      onActiveContextOptionsChange,
    ]) {
      expect(cb).not.toHaveBeenCalled();
    }
  });

  it("rings the mode and model pills when they have keyboard focus", async () => {
    renderStatefulSelector();
    await chooseModel(/Jev 1.13/);
    for (const name of [
      "Evaluation mode: Quick",
      "Evaluation model: Jev 1.13 (pinned)",
    ]) {
      const pill = screen.getByRole("button", { name });
      expect(focusRing(pill)).toMatch(/outline: 2px solid/);
      // The ring shows at once: a transition on "all" grew it from a 0 px
      // outline in the text colour, which read as a darker border.
      expect(transitionOf(pill)).toMatch(/0\.15s/);
      expect(transitionOf(pill)).not.toMatch(/\ball\b|outline/);
    }
  });

  it("opens mode, model and configuration with native keyboard button activation", async () => {
    renderStatefulSelector();
    const mode = screen.getByRole("button", { name: "Evaluation mode: Agent" });
    mode.focus();
    await userEvent.keyboard("{Enter}");
    const agent = await screen.findByRole("menuitem", { name: /^Agent/ });
    await waitFor(() => expect(agent).toHaveFocus());
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: /^Auto/ })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByTestId("parent-mode")).toHaveTextContent("auto");
    await waitFor(() => expect(mode).toHaveFocus());
    const model = screen.getByRole("button", {
      name: "Evaluation model: local-model",
    });
    model.focus();
    await userEvent.keyboard(" ");
    await screen.findByRole("menuitem", { name: /Jev latest/ });
    await waitFor(() =>
      expect(screen.getByPlaceholderText("Search models...")).toHaveFocus(),
    );
    await userEvent.keyboard("{ArrowDown}");
    expect(
      screen.getByRole("menuitem", { name: /Turing Large/ }),
    ).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /Jev 1.13/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(
      screen.getByRole("button", { name: "Evaluation model: Jev latest" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(model).toHaveFocus());
    const config = screen.getByRole("button", { name: "Configure evaluation" });
    config.focus();
    await userEvent.keyboard("{Enter}");
    expect(
      await screen.findByRole("menuitem", { name: /^Use Internet/ }),
    ).toBeInTheDocument();
  });

  it("navigates model sections with arrows and skips locked models", async () => {
    capabilityResponse = Promise.resolve({
      data: {
        features: {
          turing_models: { allowed: false },
          jev_models: { allowed: true },
        },
      },
    });
    renderStatefulSelector();
    const trigger = screen.getByRole("button", {
      name: "Evaluation model: local-model",
    });
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("menuitem", { name: /local-model/ });
    await waitFor(() =>
      expect(
        screen.getByRole("menuitem", { name: /Turing Large/ }),
      ).toHaveAttribute("aria-disabled", "true"),
    );
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /Jev 1.13/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: /local-model/ })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("menuitem", { name: /Jev 1.13/ })).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("menuitem", { name: /local-model/ })).toHaveFocus();
    await userEvent.keyboard("{Tab}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(
      screen.queryByRole("menu", { name: "Evaluation models" }),
    ).toBeNull();

    await userEvent.keyboard("{Enter}");
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search models" }),
      "Jev",
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("menuitem", { name: /local-model/ }),
      ).toBeNull(),
    );
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: /Jev latest/ })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(
      screen.getByRole("button", { name: "Evaluation model: Jev latest" }),
    ).toBeInTheDocument();
  });

  it("focuses configuration options and toggles Internet with the keyboard", async () => {
    renderStatefulSelector();
    const trigger = screen.getByRole("button", {
      name: "Configure evaluation",
    });
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    const internet = await screen.findByRole("menuitem", {
      name: /^Use Internet/,
    });
    await waitFor(() => expect(internet).toHaveFocus());
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: /^Connectors/ })).toHaveFocus();
    await userEvent.keyboard("{Home}{Enter}{Escape}");
    expect(screen.getByText("Internet")).toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("disables every primary selector when the editor is disabled", () => {
    renderSelector({ disabled: true });
    for (const name of [
      /^Evaluation mode:/,
      /^Evaluation model:/,
      "Configure evaluation",
    ]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });
});


describe("ModelSelector union — Jev with Falcon unavailable", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockModelList([]);
  });

  it.each(["denied", "loading", "failed"])(
    "keeps Jev options disabled and makes no connector request while Falcon is %s",
    async (state) => {
      capabilityResponse = state === "loading"
        ? new Promise(() => {})
        : state === "failed"
          ? Promise.reject(new Error("capabilities unavailable"))
          : Promise.resolve({ data: { features: {
              jev_models: { allowed: true },
              falcon_ai: { allowed: false },
            } } });
      renderSelector({ model: "jev-latest" });
      await userEvent.click(screen.getByRole("button", { name: "Configure evaluation" }));
      for (const name of [/^Use Internet/, /^Knowledge Base/, /^Data Injection/]) {
        expect(await screen.findByRole("menuitem", { name }))
          .toHaveAttribute("aria-disabled", "true");
      }
      expect(screen.queryByRole("menuitem", { name: /^Connectors/ })).toBeNull();
      expect(screen.queryByRole("menuitem", { name: /^Summary/ })).toBeNull();
      expect(mockGet.mock.calls.some(([url]) => url === "/falcon-ai/connectors/"))
        .toBe(false);
    },
  );
});
