import { describe, it, expect, beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import NewObserve from "../NewObserve";
import NewExperiment from "../NewExperiment";

// The "Welcome to Observe" setup page says "check out our Docs" (the
// verb, not the noun "checkout"), and its section headings are in sentence
// case: "Install dependencies", "Set up telemetry", "Set up instrumentation".
// The experiment setup panel uses the same copy, so it is checked too.

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get },
  endpoints: {
    project: { getCodeBlockTracer: "/tracer/project/project_sdk_code/" },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  trackEvent: vi.fn(),
  handleOnDocsClicked: vi.fn(),
}));

vi.mock("react-syntax-highlighter", () => ({
  // eslint-disable-next-line react/prop-types
  Prism: ({ children }) => <pre>{children}</pre>,
}));

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));

const fixture = {
  installation_guide: { Python: "INSTALL_PY", TypeScript: "INSTALL_TS" },
  project_add_code: { Python: "TELEMETRY_PY", TypeScript: "TELEMETRY_TS" },
  keys: { Python: "KEYS_PY", TypeScript: "KEYS_TS" },
  instruments: {},
};

const HEADINGS = [
  "Install dependencies",
  "Set up telemetry",
  "Set up instrumentation",
];
const OLD_HEADINGS = [
  "Install Dependencies",
  "Setup Telemetry",
  "Setup Instrumentation",
];
const DOCS_LINE = "For more instructions, check out our Docs";

const renderPage = (Page) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Page />
    </QueryClientProvider>,
  );
};

const expectSetupCopy = async (Page) => {
  const { container } = renderPage(Page);

  await waitFor(() => expect(container.textContent).toContain("TELEMETRY_PY"));
  for (const heading of HEADINGS) {
    expect(screen.getByText(heading)).toBeInTheDocument();
  }
  for (const heading of OLD_HEADINGS) {
    expect(screen.queryByText(heading)).toBeNull();
  }
  // The docs line is the description text followed by its Docs link, so it
  // is read as one line with the whitespace between them collapsed.
  const docs = screen.getByRole("link", { name: "Docs" });
  const docsLine = docs.parentElement.textContent.replace(/\s+/g, " ").trim();
  expect(docsLine).toBe(DOCS_LINE);
  expect(container.textContent).not.toContain("checkout our");
};

describe("Setup page copy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({ data: { result: fixture } });
  });

  it("the Observe setup page says check out our Docs and uses sentence-case headings", async () => {
    await expectSetupCopy(NewObserve);
  });

  it("the experiment setup panel uses the same copy", async () => {
    await expectSetupCopy(NewExperiment);
  });
});
