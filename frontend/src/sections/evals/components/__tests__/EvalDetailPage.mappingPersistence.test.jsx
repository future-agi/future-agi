import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvalDetailPage from "../EvalDetailPage";
import { endpoints } from "src/utils/axios";
// EvalDetailPage reads app-specific palette extensions (e.g. `amber`) that a
// bare `createTheme()` doesn't have — use the real palette so styling code
// doesn't crash on an undefined lookup.
import { palette } from "src/theme/palette";

const testTheme = createTheme({ palette: palette("light") });

// TH-7114 — real call-path coverage for EvalDetailPage's own half of the fix:
//   1. `testMapping` / `testTracingProjectId` (useMemo off viewingVersion ||
//      defaultVersion) recompute per version.
//   2. `key={viewingVersion?.id ?? "live"}` on <TestPlayground> forces a full
//      remount on version switch — without it, TracingTestMode/DatasetTestMode
//      would keep whatever they seeded on first mount forever (both apply
//      initialMapping/initialTracingProjectId exactly once, via a `useState`
//      initializer — see TracingTestMode.jsx / DatasetTestMode.jsx).
//   3. `handleSaveVersion` reads `testPlaygroundRef.current.getMappingState()`
//      and merges `buildVersionMappingPayload(...)` into the create-version
//      request.
//
// TestPlayground itself already has real-component coverage in
// TestPlayground.mappingPersistence.test.jsx (real TracingTestMode/
// DatasetTestMode, no mocks) — mocking it here is scoped ONLY to isolate
// EvalDetailPage's own wiring. The mock deliberately reproduces the same
// "seed once via a useState initializer" contract the real children use
// (not a made-up shape), so this test still goes red if the remount key is
// dropped — see the assertion in "remounts test panel on version switch".

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPutMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

const { v1, v2 } = vi.hoisted(() => ({
  v1: {
    id: "v1",
    version_number: 1,
    is_default: false,
    mapping: { question: "attributes.input.value.v1" },
    tracing_project_id: "project-A",
    config_snapshot: {},
  },
  v2: {
    id: "v2",
    version_number: 2,
    is_default: true,
    mapping: { question: "attributes.input.value.v2" },
    tracing_project_id: "project-B",
    config_snapshot: {},
  },
}));

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: {
      ...actual.default,
      get: axiosGetMock,
      put: axiosPutMock,
      post: axiosPostMock,
    },
  };
});

vi.mock("notistack", async () => {
  const actual = await vi.importActual("notistack");
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));

vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({ isOSS: false }),
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  default: ({ leftPanel, rightPanel }) => (
    <div>
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));

vi.mock("../CodeEvalEditor", () => ({
  default: ({ setCode }) => (
    <button type="button" onClick={() => setCode("print('edited')")}>
      mock edit code
    </button>
  ),
}));

vi.mock("../OutputTypeConfig", () => ({ default: () => null }));
vi.mock("../BulkDeleteDialog", () => ({ default: () => null }));

// Mirrors the real seed-once contract in TracingTestMode.jsx / DatasetTestMode.jsx:
//   const [mapping, setMapping] = useState(() => initialMapping ? {...initialMapping} : {});
// i.e. captured ONLY on first mount of a given instance — proving the parent
// must remount (via `key=`) rather than merely re-render to re-seed it.
vi.mock("../TestPlayground", () => {
  const MockTestPlayground = React.forwardRef(
    ({ initialMapping, initialTracingProjectId, onVersionSelect }, ref) => {
      const [seededMapping] = React.useState(() => initialMapping || {});
      const [seededProjectId] = React.useState(
        () => initialTracingProjectId || null,
      );

      React.useImperativeHandle(ref, () => ({
        getMappingState: () => ({
          // Simulates the user having the Tracing tab's live mapping state
          // (what a real TracingTestMode.getMappingState() would return)
          // rather than just echoing what was seeded, so the save-path
          // assertion is against a distinct, attributable value.
          mapping: { ...seededMapping, live_edit: "attributes.output.value" },
          tracingProjectId: seededProjectId,
        }),
        switchToVersion: () => {},
      }));

      return (
        <div>
          <div data-testid="seeded-mapping">
            {JSON.stringify(seededMapping)}
          </div>
          <div data-testid="seeded-project">{seededProjectId ?? "null"}</div>
          <button type="button" onClick={() => onVersionSelect?.(v1)}>
            mock switch to v1
          </button>
        </div>
      );
    },
  );
  MockTestPlayground.displayName = "MockTestPlayground";
  MockTestPlayground.propTypes = {
    initialMapping: PropTypes.object,
    initialTracingProjectId: PropTypes.string,
    onVersionSelect: PropTypes.func,
  };
  return { default: MockTestPlayground };
});

const evalDetail = {
  id: "eval-1",
  name: "Test eval",
  eval_type: "code",
  owner: "user",
  template_type: "single",
  config: {},
};

const renderEvalDetail = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={testTheme}>
        <MemoryRouter initialEntries={["/dashboard/evals/eval-1"]}>
          <Routes>
            <Route
              path="/dashboard/evals/:evalId"
              element={<EvalDetailPage />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
};

describe("EvalDetailPage → version-scoped mapping persistence (TH-7114)", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockImplementation(async (url) => {
      if (url === endpoints.develop.eval.getEvalDetail("eval-1")) {
        return { data: { result: evalDetail } };
      }
      if (url === endpoints.develop.eval.getEvalVersions("eval-1")) {
        return {
          data: {
            result: { template_id: "eval-1", versions: [v1, v2], total: 2 },
          },
        };
      }
      return { data: { result: null } };
    });

    axiosPutMock.mockReset();
    axiosPutMock.mockResolvedValue({ data: { result: {} } });

    axiosPostMock.mockReset();
    axiosPostMock.mockResolvedValue({
      data: { result: { id: "v3", version_number: 3 } },
    });
  });

  it("seeds the test panel from the DEFAULT version's saved mapping on load", async () => {
    renderEvalDetail();

    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v2.mapping),
      );
    });
    expect(screen.getByTestId("seeded-project")).toHaveTextContent("project-B");
  });

  it("remounts the test panel on version switch so it re-seeds from the NEWLY viewed version (not the stale one)", async () => {
    renderEvalDetail();

    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v2.mapping),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /mock switch to v1/i }));

    // If `key={viewingVersion?.id ?? "live"}` were dropped from EvalDetailPage,
    // the mock (like the real TracingTestMode/DatasetTestMode) would keep its
    // v2-seeded state forever, since a `useState` initializer only runs once
    // per mounted instance — this assertion would then see v2's mapping
    // still, not v1's, and fail.
    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v1.mapping),
      );
    });
    expect(screen.getByTestId("seeded-project")).toHaveTextContent("project-A");
  });

  it("Save Version reads the test panel's live mapping/project and sends it in the create-version payload", async () => {
    renderEvalDetail();

    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v2.mapping),
      );
    });

    // Dirty the form so Save Version becomes enabled.
    fireEvent.click(screen.getByRole("button", { name: /mock edit code/i }));

    const saveButton = await screen.findByRole("button", {
      name: /save version/i,
    });
    await waitFor(() => expect(saveButton).toBeEnabled());
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(axiosPostMock).toHaveBeenCalledWith(
        endpoints.develop.eval.createEvalVersion("eval-1"),
        expect.objectContaining({
          mapping: expect.objectContaining({
            live_edit: "attributes.output.value",
          }),
          tracing_project_id: "project-B",
        }),
      );
    });
  });
});
