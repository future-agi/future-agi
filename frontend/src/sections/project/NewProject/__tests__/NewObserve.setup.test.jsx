import { describe, it, expect, beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import NewObserve from "../NewObserve";

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

// Render the raw code text instead of running the syntax highlighter.
vi.mock("react-syntax-highlighter", () => ({
  // eslint-disable-next-line react/prop-types
  Prism: ({ children }) => <pre>{children}</pre>,
}));

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));

const tracerFixture = {
  installation_guide: {
    Python: "INSTALL_GUIDE_PY_MARKER",
    TypeScript: "INSTALL_GUIDE_TS_MARKER",
  },
  project_add_code: {
    Python: "TELEMETRY_PY_MARKER",
    TypeScript: "TELEMETRY_TS_MARKER",
  },
  keys: { Python: "API_KEYS_PY_MARKER", TypeScript: "API_KEYS_TS_MARKER" },
  instruments: {},
};

// What src/utils/axios rejects with when the request never gets a response.
const networkError = {
  message: "Network Error",
  statusCode: undefined,
  transportCode: "ERR_NETWORK",
};

const renderObserve = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NewObserve />
    </QueryClientProvider>,
  );
};

describe("NewObserve setup instructions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({ data: { result: tracerFixture } });
  });

  it("OBS-SW-105: says the project name in the code picks or creates the project", async () => {
    const { container } = renderObserve();

    await waitFor(() =>
      expect(container.textContent).toContain("TELEMETRY_PY_MARKER"),
    );
    expect(
      screen.getByText(
        /^Set project_name to the project you want\. Observe creates it when its first trace arrives; reuse a name to send to an existing project\./,
      ),
    ).toBeInTheDocument();
    // No project exists or is selected on these pages.
    expect(container.textContent).not.toContain("to this project");

    fireEvent.click(screen.getAllByRole("tab", { name: "Typescript" })[0]);

    await waitFor(() =>
      expect(container.textContent).toContain("TELEMETRY_TS_MARKER"),
    );
    expect(
      screen.getByText(/^Set projectName to the project you want\./),
    ).toBeInTheDocument();
  });

  it("OBS-SW-106: shows an error with Retry when the setup code request fails, and Retry loads it", async () => {
    mocks.get.mockRejectedValueOnce(networkError);
    const { container } = renderObserve();

    expect(
      await screen.findByText("Could not load setup instructions"),
    ).toBeInTheDocument();
    expect(screen.getByText("Network Error")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(container.textContent).toContain("TELEMETRY_PY_MARKER"),
    );
    expect(
      screen.queryByText("Could not load setup instructions"),
    ).not.toBeInTheDocument();
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });
});
