import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EvalDetailPage from "../EvalDetailPage";
import { endpoints } from "src/utils/axios";
// EvalDetailPage reads app-specific palette extensions (e.g. `amber`) that a
// bare `createTheme()` doesn't have — use the real palette so styling code
// doesn't crash on an undefined lookup.
import { palette } from "src/theme/palette";

const testTheme = createTheme({ palette: palette("light") });

// Save Version calls switchToVersion, which unmounts the active test mode. A
// test in flight loses the onTestResult callback that clears `isTesting`, so
// the button stays on "Running..." forever; a 60s fallback clears it.
//
// Mounts the REAL EvalDetailPage and mocks ONLY TestPlayground, honoring its
// real contract. From EvalDetailPage's boundary the unmount is observable only
// as "onTestResult never fires", so the interrupted path is driven by a real
// Save Version while withholding onTestResult.

const axiosGetMock = vi.hoisted(() => vi.fn());
const axiosPutMock = vi.hoisted(() => vi.fn());
const axiosPostMock = vi.hoisted(() => vi.fn());
// Records real runTest() calls coming out of handleTestEvaluation, so a test
// can await proof that execution reached the line the 60s fallback sits on.
const runTestSpy = vi.hoisted(() => vi.fn());

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

// Mock TestPlayground — honors the REAL imperative handle + callback contract
// (see TestPlayground.jsx useImperativeHandle at ~L1048 and PropTypes at
// ~L1922). It never fires onTestResult on its own: whether a test "completes"
// is driven explicitly from the test body (the "mock finish test (success)"
// button = the real onTestResult(true, result) path), so a test can withhold
// the result to reproduce the dropped-callback / unmount scenario.
vi.mock("../TestPlayground", () => {
  const MockTestPlayground = React.forwardRef(
    (
      { initialMapping, initialTracingProjectId, onReadyChange, onTestResult },
      ref,
    ) => {
      React.useImperativeHandle(ref, () => ({
        runTest: (tid) => runTestSpy(tid),
        switchToVersion: () => {},
        getMappingState: () => ({
          mapping: { ...(initialMapping || {}) },
          tracingProjectId: initialTracingProjectId ?? null,
        }),
      }));

      // Signal readiness once per mount (real TracingTestMode/DatasetTestMode
      // report up via onReadyChange) so the "Test Evaluation" button enables.
      React.useEffect(() => {
        onReadyChange?.(true, initialMapping || {});
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);

      return (
        <div>
          <button
            type="button"
            onClick={() => onTestResult?.(true, { output: "passed" })}
          >
            mock finish test (success)
          </button>
        </div>
      );
    },
  );
  MockTestPlayground.displayName = "MockTestPlayground";
  MockTestPlayground.propTypes = {
    initialMapping: PropTypes.object,
    initialTracingProjectId: PropTypes.string,
    onReadyChange: PropTypes.func,
    onTestResult: PropTypes.func,
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

const testButton = () =>
  screen.getByRole("button", { name: /test evaluation|running/i });

// Waits until the "Test Evaluation" button is present AND enabled — i.e. the
// eval detail + versions queries have resolved and the mock has reported ready.
const waitForReadyTestButton = async () => {
  await waitFor(() => {
    const btn = screen.queryByRole("button", { name: /test evaluation/i });
    expect(btn).toBeTruthy();
    expect(btn).toBeEnabled();
  });
};

// Clicks Test Evaluation and waits until handleTestEvaluation has run past the
// awaited config-save to the runTest() line — the point at which the 60s
// fallback is scheduled and isTesting is already true ("Running...").
const startTest = async () => {
  await act(async () => {
    fireEvent.click(testButton());
  });
  await waitFor(() => expect(runTestSpy).toHaveBeenCalled());
  expect(testButton()).toHaveTextContent(/running/i);
  expect(testButton()).toBeDisabled();
};

describe("EvalDetailPage → isTesting stuck-state 60s fallback", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    runTestSpy.mockReset();

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

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("clears isTesting after 60s when Save Version unmounts the test panel before the result arrives (the dropped-onTestResult path)", async () => {
    renderEvalDetail();
    await waitForReadyTestButton();

    // Dirty the config so Save Version is enabled.
    fireEvent.click(screen.getByRole("button", { name: /mock edit code/i }));
    const saveButton = await screen.findByRole("button", {
      name: /save version/i,
    });
    await waitFor(() => expect(saveButton).toBeEnabled());

    // Start a test — isTesting is now true, onTestResult deliberately NOT fired.
    await startTest();

    // Save Version → real handleSaveVersion runs, calls switchToVersion and
    // sets viewingVersion to the new version, which really remounts
    // <TestPlayground key={viewingVersion?.id}> and drops the in-flight test's
    // onTestResult — exactly the unmount the ticket describes.
    await act(async () => {
      fireEvent.click(saveButton);
    });
    await waitFor(() =>
      expect(axiosPostMock).toHaveBeenCalledWith(
        endpoints.develop.eval.createEvalVersion("eval-1"),
        expect.any(Object),
      ),
    );

    // Callback was dropped → still stuck on "Running..." right after the unmount.
    expect(testButton()).toHaveTextContent(/running/i);
    expect(testButton()).toBeDisabled();

    // Advance to the 60s fallback — isTesting must clear even though
    // onTestResult never fired.
    await act(async () => {
      vi.advanceTimersByTime(60000);
    });

    await waitFor(() => {
      expect(testButton()).toHaveTextContent(/test evaluation/i);
      expect(testButton()).toBeEnabled();
    });
  });

  it("clears isTesting immediately via the real onTestResult callback on a normal test — without the 60s fallback", async () => {
    renderEvalDetail();
    await waitForReadyTestButton();

    await startTest();

    // Normal completion: the real onTestResult(true, result) path fires.
    fireEvent.click(
      screen.getByRole("button", { name: /mock finish test \(success\)/i }),
    );

    // Cleared right away — timers NOT advanced, so the fallback did not (and
    // need not) fire. This guards the common case against a regression where
    // the fallback masks or blocks the normal success path.
    await waitFor(() => {
      expect(testButton()).toHaveTextContent(/test evaluation/i);
      expect(testButton()).toBeEnabled();
    });
    expect(vi.getTimerCount()).toBeGreaterThan(0); // 60s fallback still pending, unused

    // And advancing the clock afterwards is a no-op — isTesting stays cleared,
    // the fallback's `(v) => (v ? false : v)` guard doesn't re-toggle anything.
    await act(async () => {
      vi.advanceTimersByTime(60000);
    });
    expect(testButton()).toHaveTextContent(/test evaluation/i);
    expect(testButton()).toBeEnabled();
  });
});
