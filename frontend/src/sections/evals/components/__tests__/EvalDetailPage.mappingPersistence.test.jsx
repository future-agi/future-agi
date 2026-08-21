import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvalDetailPage from "../EvalDetailPage";
import { buildVersionMappingPayload } from "../../utils/evalMappingPersistence";
import { endpoints } from "src/utils/axios";
// EvalDetailPage reads app-specific palette extensions (e.g. `amber`) that a
// bare `createTheme()` doesn't have — use the real palette so styling code
// doesn't crash on an undefined lookup.
import { palette } from "src/theme/palette";

const testTheme = createTheme({ palette: palette("light") });

// TestPlayground has real-component coverage of its own in
// TestPlayground.mappingPersistence.test.jsx; mocking it here isolates
// EvalDetailPage's wiring. The mock reproduces the real seed contract rather
// than a made-up one, so dropping `seedKey` still goes red.

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPutMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());

const { v1, v2 } = vi.hoisted(() => ({
  v1: {
    id: "v1",
    version_number: 1,
    is_default: false,
    mapping: { tracing: { question: "attributes.input.value.v1" } },
    tracing_project_id: "project-A",
    config_snapshot: {},
  },
  v2: {
    id: "v2",
    version_number: 2,
    is_default: true,
    mapping: { tracing: { question: "attributes.input.value.v2" } },
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

vi.mock("src/hooks/useCapabilities", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
    useFeatureLocked: () => ({ locked: false, isLoading: false }),
    useCapabilities: () => ({ data: undefined, isLoading: false }),
  };
});


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

// Mirrors the real seed contract in TracingTestMode.jsx / DatasetTestMode.jsx:
// state is captured on mount and re-applied only when `seedKey` changes, never
// on an ordinary re-render. A component that re-seeded on every render would
// clobber the user's edits, so the parent has to hand over a version identity
// for this to work — drop `seedKey` and the version switch stops re-seeding.
vi.mock("../TestPlayground", () => {
  const MockTestPlayground = React.forwardRef(
    (
      { initialMapping, initialTracingProjectId, seedKey, onVersionSelect },
      ref,
    ) => {
      const [seededMapping, setSeededMapping] = React.useState(
        () => initialMapping?.tracing || {},
      );
      const [seededProjectId, setSeededProjectId] = React.useState(
        () => initialTracingProjectId || null,
      );

      const seedKeyRef = React.useRef(seedKey);
      React.useEffect(() => {
        if (seedKeyRef.current === seedKey) return;
        seedKeyRef.current = seedKey;
        setSeededMapping(initialMapping?.tracing || {});
        setSeededProjectId(initialTracingProjectId || null);
      }, [seedKey, initialMapping, initialTracingProjectId]);

      React.useImperativeHandle(ref, () => ({
        getMappingState: () => ({
          // Simulates the user having the Tracing tab's live mapping state
          // (what a real TracingTestMode.getMappingState() would return)
          // rather than just echoing what was seeded, so the save-path
          // assertion is against a distinct, attributable value.
          mapping: {
            tracing: { ...seededMapping, live_edit: "attributes.output.value" },
            dataset: {},
          },
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
    seedKey: PropTypes.string,
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

describe("EvalDetailPage → version-scoped mapping persistence", () => {
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

  // Shape parity, both directions: these fixtures stand in for rows the
  // backend returns, so they have to be the exact shape Save Version writes.
  // A hand-authored shape no writer produces is how a green test hides a real
  // break.
  it("uses the exact mapping shape Save Version actually writes", () => {
    [v1, v2].forEach((version) => {
      expect(version.mapping).toEqual(
        buildVersionMappingPayload(
          { tracing: version.mapping.tracing },
          version.tracing_project_id,
        ).mapping,
      );
    });
  });

  it("seeds the test panel from the DEFAULT version's saved mapping on load", async () => {
    renderEvalDetail();

    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v2.mapping.tracing),
      );
    });
    expect(screen.getByTestId("seeded-project")).toHaveTextContent("project-B");
  });

  it("re-seeds the test panel on version switch from the NEWLY viewed version (not the stale one)", async () => {
    renderEvalDetail();

    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v2.mapping.tracing),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /mock switch to v1/i }));

    // If `seedKey` were dropped from EvalDetailPage, the mock (like the real
    // TracingTestMode/DatasetTestMode) would keep its v2-seeded state forever,
    // since seeding only re-applies on a seedKey change — this assertion would
    // then see v2's mapping still, not v1's, and fail.
    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v1.mapping.tracing),
      );
    });
    expect(screen.getByTestId("seeded-project")).toHaveTextContent("project-A");
  });

  it("Save Version reads the test panel's live mapping/project and sends it in the create-version payload", async () => {
    renderEvalDetail();

    await waitFor(() => {
      expect(screen.getByTestId("seeded-mapping")).toHaveTextContent(
        JSON.stringify(v2.mapping.tracing),
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
            tracing: expect.objectContaining({
              live_edit: "attributes.output.value",
            }),
          }),
          tracing_project_id: "project-B",
        }),
      );
    });
  });
});
