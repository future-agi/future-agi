/* eslint-disable react/prop-types */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "src/utils/test-utils";
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
  Prism: ({ children }) => <pre>{children}</pre>,
}));

vi.mock("src/components/iconify", () => ({ default: () => <span /> }));

// Mirrors the live /tracer/project/project_sdk_code/ response (snake_case).
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

describe("NewObserve FTUX SDK code (TH-5814)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockResolvedValue({ data: { result: tracerFixture } });
  });

  it("renders install, telemetry and keys code from the snake_case response", async () => {
    const { container } = renderObserve();

    // installation_guide (regressed from installationGuide)
    await waitFor(() =>
      expect(container.textContent).toContain("INSTALL_GUIDE_PY_MARKER"),
    );
    // project_add_code (regressed from projectAddCode)
    expect(container.textContent).toContain("TELEMETRY_PY_MARKER");
    expect(container.textContent).toContain("API_KEYS_PY_MARKER");
    // a key-casing mismatch would surface as this fallback string
    expect(container.textContent).not.toContain("Code not available");
  });
});
