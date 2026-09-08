import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock("src/utils/axios", async () => {
  const actual = await vi.importActual("src/utils/axios");
  return {
    ...actual,
    default: { ...actual.default, get: mocks.get, post: mocks.post },
  };
});

vi.mock("./useMapToVariable", () => ({
  useMapToVariable: () => ({
    renderRowMapAction: () => null,
    mapMenu: null,
    rowHoverSx: {},
  }),
}));
vi.mock("../hooks/useCompositeEval", () => ({
  useExecuteCompositeEval: () => ({ mutateAsync: vi.fn() }),
  useExecuteCompositeEvalAdhoc: () => ({ mutateAsync: vi.fn() }),
}));
vi.mock("../hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn() }),
}));
vi.mock("./DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("./EvalResultDisplay", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/RequiredMark", () => ({ default: () => null }));
vi.mock("src/components/custom-audio/CustomAudioPlayer", () => ({
  default: () => null,
}));
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({ AudioPlaybackProvider: ({ children }) => children }),
);
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/draggable-col-resizer", () => ({
  default: () => null,
}));

import { endpoints } from "src/utils/axios";
import SimulationTestMode from "./SimulationTestMode";

const exactPage = ({
  results,
  total,
  cursor = null,
  loaded,
  at = "2026-08-14T00:00:00Z",
}) => ({
  exact: true,
  results,
  snapshot_total: total,
  loaded_through: loaded,
  has_more: Boolean(cursor),
  complete: !cursor,
  next_cursor: cursor,
  snapshot_at: at,
});

const execution = (id) => ({
  id,
  status: "completed",
  created_at: "2026-08-13T00:00:00Z",
});
const call = (id) => ({
  id,
  status: "completed",
  created_at: "2026-08-13T00:00:00Z",
});

function installInitialResponses() {
  mocks.get.mockImplementation((url, config = {}) => {
    if (url === endpoints.runTests.list) {
      return Promise.resolve({
        data: {
          count: 2,
          results: [
            { id: "run-1", name: "Run one" },
            { id: "run-2", name: "Run two" },
          ],
        },
      });
    }
    if (url === endpoints.runTests.detail("run-1")) {
      return Promise.resolve({ data: { id: "run-1", name: "Run one" } });
    }
    if (url === endpoints.runTests.detail("run-2")) {
      return Promise.resolve({ data: { id: "run-2", name: "Run two" } });
    }
    if (url === endpoints.runTests.previewExecutions("run-1")) {
      if (config.params?.cursor === "execution-next") {
        return Promise.resolve({
          data: exactPage({
            results: [execution("execution-2")],
            total: 2,
            loaded: 2,
          }),
        });
      }
      return Promise.resolve({
        data: exactPage({
          results: [execution("execution-1")],
          total: 2,
          loaded: 1,
          cursor: "execution-next",
        }),
      });
    }
    if (url === endpoints.runTests.previewExecutions("run-2")) {
      return Promise.resolve({
        data: exactPage({
          results: [execution("execution-3")],
          total: 2,
          loaded: 1,
          cursor: "execution-run-2-next",
        }),
      });
    }
    if (url === endpoints.testExecutions.previewCalls("execution-1")) {
      if (config.params?.cursor === "call-next") {
        return Promise.resolve({
          data: exactPage({
            results: [call("call-2")],
            total: 2,
            loaded: 2,
          }),
        });
      }
      return Promise.resolve({
        data: exactPage({
          results: [call("call-1")],
          total: 2,
          loaded: 1,
          cursor: "call-next",
        }),
      });
    }
    if (url === endpoints.testExecutions.previewCalls("execution-2")) {
      return Promise.resolve({
        data: exactPage({
          results: [call("call-3")],
          total: 2,
          loaded: 1,
          cursor: "call-execution-2-next",
        }),
      });
    }
    if (url === endpoints.testExecutions.previewCalls("execution-3")) {
      return Promise.resolve({
        data: exactPage({
          results: [call("call-4")],
          total: 1,
          loaded: 1,
        }),
      });
    }
    if (url === endpoints.runTests.callExecutionDetail("call-1")) {
      return Promise.resolve({
        data: {
          id: "call-1",
          status: "completed",
          simulation_call_type: "text",
          transcript: "user: hi\nassistant: hello",
        },
      });
    }
    if (
      url === endpoints.runTests.callExecutionDetail("call-3") ||
      url === endpoints.runTests.callExecutionDetail("call-4")
    ) {
      return Promise.resolve({
        data: {
          id: url.endsWith("call-3/") ? "call-3" : "call-4",
          status: "completed",
          simulation_call_type: "text",
          transcript: "user: hi\nassistant: hello",
        },
      });
    }
    throw new Error(`Unexpected GET ${url}`);
  });
}

describe("SimulationTestMode exact snapshot pagination", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.post.mockReset();
    installInitialResponses();
  });

  it("shows a retryable bounded error instead of publishing an empty simulation list", async () => {
    const originalImplementation = mocks.get.getMockImplementation();
    let listAttempts = 0;
    mocks.get.mockImplementation((url, config = {}) => {
      if (url === endpoints.runTests.list && listAttempts++ === 0) {
        return Promise.reject({ code: "ECONNABORTED" });
      }
      return originalImplementation(url, config);
    });

    render(<SimulationTestMode variables={[]} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Simulation loading timed out.",
    );
    expect(screen.queryByText("No simulations")).not.toBeInTheDocument();
    const firstListConfig = mocks.get.mock.calls.find(
      ([url]) => url === endpoints.runTests.list,
    )[1];
    expect(firstListConfig.timeout).toBe(30_000);
    expect(firstListConfig.signal).toBeInstanceOf(AbortSignal);
    expect(firstListConfig.params).toEqual({
      page: 1,
      limit: 50,
      summary: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "Retry simulations" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    const input = screen.getByPlaceholderText("Search simulations...");
    fireEvent.mouseDown(input);
    expect(
      await screen.findByRole("option", { name: /Run one/ }),
    ).toBeVisible();
  });

  it("uses explicit read-more for executions and calls with a 9s request wall", async () => {
    render(<SimulationTestMode initialRunTestId="run-1" variables={[]} />);

    expect(
      await screen.findByText("1 of 2 execution runs loaded"),
    ).toBeInTheDocument();
    expect(await screen.findByText(/1 of 2 loaded/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more runs" }));
    expect(
      await screen.findByText("2 of 2 execution runs loaded"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more calls" }));
    expect(await screen.findByText(/2 of 2 loaded/)).toBeInTheDocument();

    const previewRequests = mocks.get.mock.calls.filter(
      ([url]) =>
        url === endpoints.runTests.previewExecutions("run-1") ||
        url === endpoints.testExecutions.previewCalls("execution-1"),
    );
    expect(previewRequests).toHaveLength(4);
    for (const [, config] of previewRequests) {
      expect(config.timeout).toBe(30_000);
    }
    const callRequests = previewRequests.filter(
      ([url]) => url === endpoints.testExecutions.previewCalls("execution-1"),
    );
    expect(callRequests).toHaveLength(2);
    for (const [, config] of callRequests) {
      expect(config.params.run_test_id).toBe("run-1");
    }
  });

  it("keeps loaded call rows and requires restart after a 409 drift", async () => {
    const originalImplementation = mocks.get.getMockImplementation();
    mocks.get.mockImplementation((url, config = {}) => {
      if (
        url === endpoints.testExecutions.previewCalls("execution-1") &&
        config.params?.cursor === "call-next"
      ) {
        return Promise.reject({
          response: {
            status: 409,
            data: {
              code: "simulation_preview_snapshot_changed",
              restart_required: true,
            },
          },
        });
      }
      return originalImplementation(url, config);
    });

    render(<SimulationTestMode initialRunTestId="run-1" variables={[]} />);
    expect(await screen.findByText(/1 of 2 loaded/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more calls" }));

    expect(
      await screen.findByRole("button", { name: "Restart list" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 loaded/)).toBeInTheDocument();
    expect(
      screen.queryByText("No calls in this simulation"),
    ).not.toBeInTheDocument();
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: "Load more calls" }),
      ).not.toBeInTheDocument();
    });
  });

  it("clears a stale execution load-more spinner when the simulation changes", async () => {
    const originalImplementation = mocks.get.getMockImplementation();
    let resolveOldPage;
    const oldPage = new Promise((resolve) => {
      resolveOldPage = resolve;
    });
    mocks.get.mockImplementation((url, config = {}) => {
      if (
        url === endpoints.runTests.previewExecutions("run-1") &&
        config.params?.cursor === "execution-next"
      ) {
        return oldPage;
      }
      return originalImplementation(url, config);
    });

    render(<SimulationTestMode variables={[]} />);
    const input = await screen.findByPlaceholderText("Search simulations...");
    fireEvent.mouseDown(input);
    fireEvent.change(input, { target: { value: "Run one" } });
    fireEvent.click(await screen.findByRole("option", { name: /Run one/ }));

    expect(
      await screen.findByText("1 of 2 execution runs loaded"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more runs" }));
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();

    const clearButton = input
      .closest(".MuiAutocomplete-root")
      .querySelector('button[title="Clear"]');
    fireEvent.click(clearButton);
    await waitFor(() => expect(input).toHaveValue(""));
    fireEvent.mouseDown(input);
    fireEvent.change(input, { target: { value: "Run two" } });
    fireEvent.click(await screen.findByRole("option", { name: /Run two/ }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Load more runs" }),
      ).toBeEnabled();
    });

    await act(async () => {
      resolveOldPage({
        data: exactPage({
          results: [execution("execution-2")],
          total: 2,
          loaded: 2,
        }),
      });
      await Promise.resolve();
    });
    expect(
      screen.getByRole("button", { name: "Load more runs" }),
    ).toBeEnabled();
  });

  it("clears a stale call load-more spinner when the execution changes", async () => {
    render(<SimulationTestMode initialRunTestId="run-1" variables={[]} />);
    expect(await screen.findByText(/1 of 2 loaded/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more runs" }));
    expect(
      await screen.findByText("2 of 2 execution runs loaded"),
    ).toBeInTheDocument();

    const originalImplementation = mocks.get.getMockImplementation();
    let resolveOldPage;
    const oldPage = new Promise((resolve) => {
      resolveOldPage = resolve;
    });
    mocks.get.mockImplementation((url, config = {}) => {
      if (
        url === endpoints.testExecutions.previewCalls("execution-1") &&
        config.params?.cursor === "call-next"
      ) {
        return oldPage;
      }
      return originalImplementation(url, config);
    });

    fireEvent.click(screen.getByRole("button", { name: "Load more calls" }));
    expect(screen.getByRole("button", { name: "Loading…" })).toBeDisabled();

    const comboBoxes = screen.getAllByRole("combobox");
    fireEvent.mouseDown(comboBoxes[1]);
    fireEvent.click(await screen.findByRole("option", { name: /Run 2/ }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Load more calls" }),
      ).toBeEnabled();
    });

    await act(async () => {
      resolveOldPage({
        data: exactPage({
          results: [call("call-2")],
          total: 2,
          loaded: 2,
        }),
      });
      await Promise.resolve();
    });
    expect(
      screen.getByRole("button", { name: "Load more calls" }),
    ).toBeEnabled();
  });
});
