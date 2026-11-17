/* eslint-disable react/prop-types */
// A new person whose organization has Future AGI
// credits and no provider key of its own must not be led to a model picker
// with no model. Each Get started step that calls a model says so at the
// step and offers both routes: add a provider key, or create a Gateway API
// key whose calls are paid from Future AGI credits. With credits off, or the
// credits status unreadable, only the provider-key route shows, as before.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  renderWithRouter,
  screen,
  userEvent,
  within,
} from "src/utils/test-utils";

const state = vi.hoisted(() => ({
  managed: null,
  keys: null,
  // Options each useApiKeysStatus call was made with, in call order.
  keyReads: [],
  orgLevel: 15,
  orgId: "org-1",
  orgReady: true,
  role: "Owner",
}));
const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/sections/gateway/models/hooks/useManagedModels", () => ({
  useManagedModelsStatus: () => state.managed,
}));
vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: (options) => {
    state.keyReads.push(options);
    return state.keys;
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: state.orgLevel,
    currentOrganizationId: state.orgId,
    isReady: state.orgReady,
  }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", organization_role: state.role },
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/components/external-content/ExternalContentFrame", () => ({
  default: () => null,
}));
vi.mock("src/components/image", () => ({
  default: ({ alt }) => <img alt={alt} />,
}));
vi.mock("src/sections/develop-detail/Common/ConfigureKeys/KeyCard", () => ({
  default: ({ data }) => <div>{`${data.display_name} key card`}</div>,
}));
vi.mock("src/components/custom-model-dropdown/CustomModalKeyCard", () => ({
  default: () => null,
}));
vi.mock("src/components/custom-model-dropdown/CloudProviderCard", () => ({
  default: () => null,
}));

const { endpoints } = await import("src/utils/axios");
const { default: KeySection } = await import(
  "../setup-steps/InitialSetupOptions/KeySection"
);
const { default: RunFirstExperiment } = await import(
  "../setup-steps/InitialSetupOptions/RunFirstExperiment"
);
const { default: CreateFirstEvaluation } = await import(
  "../setup-steps/InitialSetupOptions/CreateFirstEvaluation"
);
const { default: CreateFirstDataset } = await import(
  "../setup-steps/InitialSetupOptions/CreateFirstDataset"
);
const { default: TryOutFeatures } = await import(
  "../try-out-features/TryOutFeatures"
);

const CREATE_KEY_HREF = "/dashboard/gateway/keys?create=1";
// Calls through the organization's own provider routes on the gateway are
// not paid from credits, so the line names Future AGI models and the
// person's own code (BillingCreditsPanel: "Calls to Future AGI models are
// paid from your organization's Future AGI credits.").
const CREDITS_LINE =
  "calls your code makes with it to Future AGI models are paid from your Future AGI credits.";

const CREDITS_ON = {
  data: { available: true, enabled: true, balance_usd: 5, markup_percent: 0 },
  isSuccess: true,
  isError: false,
  isLoading: false,
};
// Credits on with no balance left: the gateway refuses calls to Future AGI
// models (HTTP 402, the gateway's Models card), and an unknown balance is
// not a funded one (the gateway's own setup check, setupReadiness).
const creditsOnWithBalance = (balance) => ({
  ...CREDITS_ON,
  data: { ...CREDITS_ON.data, balance_usd: balance },
});
const CREDITS_DISABLED = {
  data: {
    available: true,
    enabled: false,
    balance_usd: null,
    markup_percent: null,
  },
  isSuccess: true,
  isError: false,
  isLoading: false,
};
const CREDITS_NOT_OFFERED = {
  data: {
    available: false,
    enabled: false,
    balance_usd: null,
    markup_percent: null,
  },
  isSuccess: true,
  isError: false,
  isLoading: false,
};
const CREDITS_LOADING = {
  data: undefined,
  isSuccess: false,
  isError: false,
  isLoading: true,
};
const CREDITS_ERROR = {
  data: undefined,
  isSuccess: false,
  isError: true,
  isLoading: false,
};

const provider = (hasKey) => ({
  id: "openai-1",
  provider: "openai",
  display_name: "OpenAI",
  type: "text",
  hasKey,
});
const NO_KEY = {
  data: [provider(false)],
  isSuccess: true,
  isFetching: false,
};
const HAS_KEY = {
  data: [provider(true)],
  isSuccess: true,
  isFetching: false,
};

beforeEach(() => {
  state.managed = CREDITS_ON;
  state.keys = NO_KEY;
  state.keyReads = [];
  state.orgLevel = 15;
  state.orgId = "org-1";
  state.orgReady = true;
  state.role = "Owner";
  http.get.mockReset();
  http.get.mockImplementation((url) =>
    url === endpoints.settings.customModal.getCustomModal
      ? Promise.resolve({ data: { results: [] } })
      : Promise.resolve({ data: { status: true, result: {} } }),
  );
});

const renderUi = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = renderWithRouter(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    { route: "/dashboard/get-started" },
  );
  return { ...view, client };
};

// Reads the step boxes start, beyond what the page read before them.
const customModelReads = () =>
  http.get.mock.calls.filter(
    ([url]) => url === endpoints.settings.customModal.getCustomModal,
  );
const enabledKeyReads = () =>
  state.keyReads.filter((options) => options?.enabled !== false);

const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

const routes = () =>
  screen.getByRole("region", { name: "Ways to call a model" });
const noRoutes = () =>
  expect(
    screen.queryByRole("region", { name: "Ways to call a model" }),
  ).not.toBeInTheDocument();
const noCreditsLink = () => {
  expect(
    screen.queryByRole("link", { name: "Create a Gateway API key" }),
  ).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("Future AGI credits");
};
// Copy rules: no forward promise and no jargon on screen.
const BANNED = /\b(yet|will|shortly|coming soon|managed|BYOK|delegated)\b/i;
const expectPlainCopy = (node) => expect(node.textContent).not.toMatch(BANNED);
// The Gateway "Playground" URL opens the Prompts workbench, which runs on
// provider keys only, so it is never offered as a credits route.
const noPlaygroundLink = () =>
  expect(
    Array.from(document.querySelectorAll("a")).filter((a) =>
      (a.getAttribute("href") || "").includes("/gateway/playground"),
    ),
  ).toHaveLength(0);

const STEPS = [
  {
    name: "Run your first experiment",
    ui: (setCurrentLabel) => (
      <RunFirstExperiment setCurrentLabel={setCurrentLabel} />
    ),
    sentence:
      "Experiments call a model, and your organization has no provider key of its own.",
  },
  {
    name: "Create your first evaluation",
    ui: (setCurrentLabel) => (
      <CreateFirstEvaluation setCurrentLabel={setCurrentLabel} />
    ),
    sentence:
      "Evaluations can call a model you pick, and your organization has no provider key of its own.",
  },
  {
    name: "Create your first dataset",
    ui: (setCurrentLabel) => (
      <CreateFirstDataset setCurrentLabel={setCurrentLabel} />
    ),
    sentence:
      "Run prompt columns and synthetic data call a model, and your organization has no provider key of its own.",
  },
  {
    name: "Explore",
    ui: (setCurrentLabel) => (
      <TryOutFeatures onAddProviderKey={() => setCurrentLabel("addKeys")} />
    ),
    sentence:
      "Experiments and evaluations call a model, and your organization has no provider key of its own.",
  },
];

describe("Get started, Add keys: credits on", () => {
  it("offers both routes: the provider key cards and a Gateway API key on credits", async () => {
    renderUi(<KeySection setCurrentLabel={vi.fn()} />);
    await settle();

    const block = routes();
    expect(
      within(block).getByText(
        "Your organization has Future AGI credits, so there are two ways to call models.",
      ),
    ).toBeInTheDocument();
    expect(
      within(block).getByText(
        "Use your own provider key: add it in the cards below.",
      ),
    ).toBeInTheDocument();
    const link = within(block).getByRole("link", {
      name: "Create a Gateway API key",
    });
    expect(link).toHaveAttribute("href", CREATE_KEY_HREF);
    expect(block.textContent).toContain(CREDITS_LINE);
    // The provider key cards stay.
    expect(screen.getByText("OpenAI key card")).toBeInTheDocument();
    expectPlainCopy(block);
    noPlaygroundLink();
  });

  it("still offers the credits route when a provider key is already saved", async () => {
    state.keys = HAS_KEY;
    renderUi(<KeySection setCurrentLabel={vi.fn()} />);
    await settle();

    expect(
      within(routes()).getByRole("link", { name: "Create a Gateway API key" }),
    ).toHaveAttribute("href", CREATE_KEY_HREF);
  });

  it("a Viewer is told who adds a provider key", async () => {
    state.orgLevel = 1;
    state.role = "Viewer";
    renderUi(<KeySection setCurrentLabel={vi.fn()} />);
    await settle();

    const block = routes();
    expect(
      within(block).getByText(
        /Ask an organization admin to add a provider key/,
      ),
    ).toBeInTheDocument();
    expect(
      within(block).queryByText(
        "Use your own provider key: add it in the cards below.",
      ),
    ).not.toBeInTheDocument();
    // Every member creates their own Gateway API key, Viewers included.
    expect(
      within(block).getByRole("link", { name: "Create a Gateway API key" }),
    ).toHaveAttribute("href", CREATE_KEY_HREF);
  });
});

describe.each([
  ["credits switched off for the organization", CREDITS_DISABLED],
  ["credits not offered on this deployment", CREDITS_NOT_OFFERED],
  ["the credits status still loading", CREDITS_LOADING],
  ["the credits status failing to load", CREDITS_ERROR],
  ["credits enabled with no balance left (0)", creditsOnWithBalance(0)],
  ['credits enabled with no balance left ("0")', creditsOnWithBalance("0")],
  ["credits enabled with a negative balance", creditsOnWithBalance(-1)],
  [
    "credits enabled with an unknown balance (null)",
    creditsOnWithBalance(null),
  ],
  ["credits enabled with an unreadable balance", creditsOnWithBalance("n/a")],
])("Get started with %s", (_label, managed) => {
  beforeEach(() => {
    state.managed = managed;
  });

  it("Add keys shows the provider key route only, as before", async () => {
    renderUi(<KeySection setCurrentLabel={vi.fn()} />);
    await settle();

    expect(screen.getByText("Add provider keys")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Add a key for each model provider you use so your prompts and experiments can call its models.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("OpenAI key card")).toBeInTheDocument();
    noRoutes();
    noCreditsLink();
  });

  it.each(STEPS.map((step) => [step.name, step]))(
    "%s shows no credits route",
    async (_name, step) => {
      renderUi(step.ui(vi.fn()));
      await settle();

      noRoutes();
      noCreditsLink();
    },
  );

  // Before this change these steps read neither the provider keys nor the
  // custom models; with no credits route to offer they still read nothing.
  it.each(STEPS.map((step) => [step.name, step]))(
    "%s starts no provider key or custom model read",
    async (_name, step) => {
      renderUi(step.ui(vi.fn()));
      await settle();

      expect(enabledKeyReads()).toEqual([]);
      expect(customModelReads()).toEqual([]);
    },
  );
});

describe("Get started, credits on with a balance given as text", () => {
  it("counts a positive balance as funded", async () => {
    state.managed = creditsOnWithBalance("12.50");
    renderUi(STEPS[0].ui(vi.fn()));
    await settle();

    expect(
      within(routes()).getByRole("link", { name: "Create a Gateway API key" }),
    ).toHaveAttribute("href", CREATE_KEY_HREF);
  });
});

// The feature cards sit under every step. When the open step already shows
// the two routes, the cards do not repeat them.
describe("Get started, Try out our features under a step with its own routes", () => {
  it("adds no second box", async () => {
    renderUi(
      <TryOutFeatures onAddProviderKey={vi.fn()} showModelRoutes={false} />,
    );
    await settle();

    noRoutes();
    expect(screen.getByText("Explore")).toBeInTheDocument();
  });
});

// Add keys reads the provider keys and custom models itself, as before, and
// a failed read there raises the app-wide error toast (no errorHandled). The
// boxes must not mark those shared reads as handled: a query's meta is
// written by every observer of it, disabled ones included.
describe.each([
  ["credits on", CREDITS_ON],
  ["credits off", CREDITS_DISABLED],
])("Get started, Add keys with %s", (_label, managed) => {
  it("keeps the app-wide error toast for its own reads", async () => {
    state.managed = managed;
    const { client } = renderUi(
      <>
        <KeySection setCurrentLabel={vi.fn()} />
        <TryOutFeatures onAddProviderKey={vi.fn()} showModelRoutes={false} />
      </>,
    );
    await settle();

    const customModels = client
      .getQueryCache()
      .find({ queryKey: ["customModals"] });
    expect(customModels).toBeDefined();
    expect(customModels.options.meta?.errorHandled).not.toBe(true);
    expect(
      state.keyReads.filter((options) => options?.meta?.errorHandled),
    ).toEqual([]);
    // KeySection's own reads still run.
    expect(customModelReads().length).toBeGreaterThan(0);
    expect(enabledKeyReads().length).toBeGreaterThan(0);
  });
});

describe.each(STEPS.map((step) => [step.name, step]))(
  "Get started, %s, credits on",
  (_name, step) => {
    it("with no provider key, says so at the step and offers both routes", async () => {
      const setCurrentLabel = vi.fn();
      const user = userEvent.setup();
      renderUi(step.ui(setCurrentLabel));
      await settle();

      const block = routes();
      expect(within(block).getByText(step.sentence)).toBeInTheDocument();
      const link = within(block).getByRole("link", {
        name: "Create a Gateway API key",
      });
      expect(link).toHaveAttribute("href", CREATE_KEY_HREF);
      expect(block.textContent).toContain(CREDITS_LINE);
      expectPlainCopy(block);
      noPlaygroundLink();

      // The provider-key route opens the Add keys step on this page.
      await user.click(
        within(block).getByRole("button", { name: "Add a provider key" }),
      );
      expect(setCurrentLabel).toHaveBeenCalledWith("addKeys");
    });

    it("with a provider key saved, adds nothing", async () => {
      state.keys = HAS_KEY;
      renderUi(step.ui(vi.fn()));
      await settle();

      noRoutes();
    });

    it("while the provider keys load or fail, adds nothing", async () => {
      state.keys = { data: undefined, isSuccess: false, isFetching: true };
      renderUi(step.ui(vi.fn()));
      await settle();

      noRoutes();
    });

    it("with a custom model saved, adds nothing", async () => {
      http.get.mockImplementation((url) =>
        url === endpoints.settings.customModal.getCustomModal
          ? Promise.resolve({ data: { results: [{ id: "cm-1" }] } })
          : Promise.resolve({ data: { status: true, result: {} } }),
      );
      renderUi(step.ui(vi.fn()));
      await settle();

      noRoutes();
    });

    it("a Viewer gets no button to a page their role cannot use", async () => {
      state.orgLevel = 1;
      state.role = "Viewer";
      renderUi(step.ui(vi.fn()));
      await settle();

      const block = routes();
      expect(
        within(block).queryByRole("button", { name: "Add a provider key" }),
      ).not.toBeInTheDocument();
      expect(
        within(block).getByText(
          /Ask an organization admin to add a provider key/,
        ),
      ).toBeInTheDocument();
      expect(
        within(block).getByRole("link", { name: "Create a Gateway API key" }),
      ).toHaveAttribute("href", CREATE_KEY_HREF);
    });

    it("a person whose membership is not known gets no Gateway link", async () => {
      state.orgLevel = null;
      state.role = undefined;
      renderUi(step.ui(vi.fn()));
      await settle();

      const block = routes();
      expect(
        within(block).queryByRole("link", { name: "Create a Gateway API key" }),
      ).not.toBeInTheDocument();
      expect(
        within(block).getByText(
          `Ask an organization admin for a Gateway API key: ${CREDITS_LINE}`,
        ),
      ).toBeInTheDocument();
    });

    // The API keys page's own rule for creating a key: the organization is
    // loaded and known, and the person is an active member.
    it.each([
      ["the organization is still loading", { orgReady: false }],
      ["no current organization is known", { orgId: null }],
    ])("gets no Gateway link while %s", async (_label, org) => {
      Object.assign(state, org);
      renderUi(step.ui(vi.fn()));
      await settle();

      const block = routes();
      expect(
        within(block).queryByRole("link", { name: "Create a Gateway API key" }),
      ).not.toBeInTheDocument();
      expect(
        within(block).getByText(
          `Ask an organization admin for a Gateway API key: ${CREDITS_LINE}`,
        ),
      ).toBeInTheDocument();
    });

    it("reads the provider keys and custom models only once credits are on", async () => {
      renderUi(step.ui(vi.fn()));
      await settle();

      expect(enabledKeyReads().length).toBeGreaterThan(0);
      expect(
        enabledKeyReads().every((options) => options?.enabled === true),
      ).toBe(true);
      expect(customModelReads().length).toBeGreaterThan(0);
    });
  },
);
