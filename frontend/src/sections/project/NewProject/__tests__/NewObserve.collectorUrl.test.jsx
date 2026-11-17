import { describe, it, expect, beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "src/utils/test-utils";
import NewObserve from "../NewObserve";

// On a self-hosted deployment reached through a public address that has not
// published its collector address, the API keeps YOUR_FI_COLLECTOR_URL in the
// keys snippet and sets collector_url_missing. The page then says so once
// under the snippet.

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

const NOTE =
  /This deployment has not published its trace collector address yet, so the example keeps YOUR_FI_COLLECTOR_URL\. Ask your admin for the address \(set as FI_COLLECTOR_PUBLIC_URL\)\./;

const fixture = (missing) => ({
  installation_guide: { Python: "INSTALL_PY", TypeScript: "INSTALL_TS" },
  project_add_code: { Python: "TELEMETRY_PY", TypeScript: "TELEMETRY_TS" },
  keys: {
    Python: missing
      ? 'os.environ["FI_BASE_URL"] = "YOUR_FI_COLLECTOR_URL"'
      : 'os.environ["FI_BASE_URL"] = "https://otlp.example.com"',
    TypeScript: "KEYS_TS",
  },
  instruments: {},
  ...(missing === undefined ? {} : { collector_url_missing: missing }),
});

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

describe("NewObserve collector address", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("says once, under the keys snippet, that the collector address is not published", async () => {
    mocks.get.mockResolvedValue({ data: { result: fixture(true) } });
    const { container } = renderObserve();

    await waitFor(() =>
      expect(container.textContent).toContain("TELEMETRY_PY"),
    );
    expect(screen.getAllByText(NOTE)).toHaveLength(1);
    expect(screen.getByRole("note")).toHaveTextContent(NOTE);
  });

  it("stays quiet when the deployment publishes its collector", async () => {
    mocks.get.mockResolvedValue({ data: { result: fixture(false) } });
    const { container } = renderObserve();

    await waitFor(() =>
      expect(container.textContent).toContain("TELEMETRY_PY"),
    );
    expect(screen.queryByText(NOTE)).toBeNull();
  });

  it("stays quiet when an older API does not send the flag", async () => {
    mocks.get.mockResolvedValue({ data: { result: fixture(undefined) } });
    const { container } = renderObserve();

    await waitFor(() =>
      expect(container.textContent).toContain("TELEMETRY_PY"),
    );
    expect(screen.queryByText(NOTE)).toBeNull();
  });
});
