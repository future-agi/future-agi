/* eslint-disable react/prop-types */
// WALK-A: "'Run your first experiment' shows the same tracing setup as the
// observability step ('Install dependencies', 'pip install
// fi-instrumentation-otel', 'Configure your application to send traces to
// Future AGI') instead of anything about experiments." The step first says how
// to run an experiment in the app, in the words its button and hint use.
// Production also gives this step the SDK setup panel (install commands with
// Python / TypeScript tabs, a personal API key control, the telemetry code);
// it stays, below the three steps, under its own heading.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderWithRouter, screen, within } from "src/utils/test-utils";

const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: http,
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { id: "person-a", organization_role: "Owner" },
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
// The video card's thumbnail (a lazy image) is not under test.
vi.mock("src/components/image", () => ({
  default: ({ alt }) => <img alt={alt} />,
}));
vi.mock("react-syntax-highlighter", () => ({
  Prism: ({ children }) => <pre>{children}</pre>,
}));

const { endpoints } = await import("src/utils/axios");
const { default: RunFirstExperiment } = await import("../RunFirstExperiment");
const { default: InitialSetupOptions } = await import("../InitialSetupOptions");

// What the tracing code-block endpoint answers for project_type=experiment.
const TRACING_SETUP = {
  installation_guide: {
    Python: "pip install fi-instrumentation-otel",
    TypeScript: "npm install @traceai/fi-core",
  },
  project_add_code: { Python: "register(...)", TypeScript: "register(...)" },
  keys: { Python: "FI_API_KEY=...", TypeScript: "FI_API_KEY=..." },
  instruments: {},
};

beforeEach(() => {
  http.get.mockReset();
  http.get.mockImplementation((url) =>
    url === endpoints.project.getCodeBlockTracer
      ? Promise.resolve({ data: { status: true, result: TRACING_SETUP } })
      : Promise.resolve({ data: { status: true, result: {} } }),
  );
});

const renderStep = (ui = <RunFirstExperiment setCurrentLabel={vi.fn()} />) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderWithRouter(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
    { route: "/dashboard/get-started?tab=RunFirstExperiment" },
  );
};

// Lets any request the step makes answer and render before asserting.
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 50));
  });

const SDK_HEADING = "Or run one from your code";

describe("Get started: Run your first experiment", () => {
  it("shows the SDK setup panel below the three steps, under its own heading", async () => {
    const { container } = renderStep();
    await settle();
    await settle();

    const panel = screen.getByRole("region", { name: SDK_HEADING });
    const steps = screen.getByRole("list", { name: "Run an experiment" });
    // The three steps come first; the panel follows them.
    expect(
      steps.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(steps.contains(panel)).toBe(false);

    // Production's panel: install commands with language tabs, the personal
    // API key control, the telemetry code.
    expect(
      await within(panel).findByText("Install dependencies"),
    ).toBeInTheDocument();
    expect(panel.textContent).toContain("pip install fi-instrumentation-otel");
    const pyTabs = within(panel).getAllByRole("tab", { name: "Python" });
    const tsTabs = within(panel).getAllByRole("tab", { name: "Typescript" });
    expect(pyTabs.length).toBeGreaterThan(0);
    expect(tsTabs.length).toBeGreaterThan(0);
    expect(
      within(panel).getByText("Use a personal API key"),
    ).toBeInTheDocument();
    expect(
      within(panel).getByRole("button", { name: "Create a key" }),
    ).toBeInTheDocument();
    expect(within(panel).getByText("Set up telemetry")).toBeInTheDocument();
    expect(panel.textContent).toContain("register(...)");
    expect(container.textContent).toContain(SDK_HEADING);
    // The panel asks for the experiment setup code, as production does.
    const setupCode = endpoints.project.getCodeBlockTracer;
    expect(http.get).toHaveBeenCalledWith(setupCode, {
      params: { project_type: "experiment" },
    });
  });

  it("says how to run an experiment in three steps, and keeps code to the SDK panel below them", async () => {
    const { container } = renderStep();
    await settle();
    await settle();

    const steps = screen.getByRole("list", { name: "Run an experiment" });
    expect(
      within(steps)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      // A user who skipped the dataset step has no dataset to open yet.
      "Open a dataset in Datasets, or create one.",
      "Open its Experiments tab.",
      "Click New experiment, pick what to run on the dataset, and compare the results.",
    ]);
    expect(steps.querySelector("pre, code")).toBeNull();
    const panel = screen.getByRole("region", { name: SDK_HEADING });
    await within(panel).findByText("Install dependencies");
    const code = [...container.querySelectorAll("pre, code")];
    expect(code.length).toBeGreaterThan(0);
    expect(code.every((block) => panel.contains(block))).toBe(true);
  });

  it("with no dataset yet, the step and its bottom bar say each instruction once", async () => {
    // The step as a person sees it: its content and the bar below it.
    renderStep(
      <InitialSetupOptions
        currentLabel="RunFirstExperiment"
        setCurrentLabel={vi.fn()}
      />,
    );
    await settle();

    expect(
      screen.getByRole("button", { name: "Go to Datasets" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Experiments tab/)).toHaveLength(1);
    expect(screen.getAllByText(/Open a dataset/)).toHaveLength(1);
  });

  it("the video card's buttons say what they do", async () => {
    renderStep();
    await settle();

    expect(
      screen.getByRole("button", { name: "Watch video" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View docs" })).toHaveAttribute(
      "href",
      "https://docs.futureagi.com/docs/dataset/features/experiments",
    );
    expect(
      screen.queryByRole("button", { name: "Check Now" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("View Doc")).not.toBeInTheDocument();
  });

  it("keeps the step's own title, line and Skip", async () => {
    renderStep();
    await settle();

    expect(screen.getByText("Run your first experiment")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Run an experiment on a dataset to compare prompts and models side by side and keep the one that works best.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });
});
