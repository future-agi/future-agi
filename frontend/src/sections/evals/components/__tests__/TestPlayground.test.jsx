/* eslint-disable react/prop-types, react/display-name */
import React, { useRef } from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import TestPlayground from "../TestPlayground";

const axiosPostMock = vi.hoisted(() => vi.fn());
const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { post: (...args) => axiosPostMock(...args), get: (...args) => axiosGetMock(...args) },
  endpoints: {
    develop: {
      eval: {
        evalPlayground: "/model-hub/eval-playground/",
        getEvalVersions: (id) => `/model-hub/eval-templates/${id}/versions/`,
        aiEvalWriter: "/model-hub/ai-eval-writer/",
        getEvalLogs: "/model-hub/get-eval-logs",
        executeCompositeEval: (id) =>
          `/model-hub/eval-templates/${id}/composite/execute/`,
        executeCompositeEvalAdhoc:
          "/model-hub/eval-templates/composite/execute-adhoc/",
        setDefaultVersion: (id, versionId) =>
          `/model-hub/eval-templates/${id}/versions/${versionId}/set-default/`,
      },
    },
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("notistack", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
  };
});

// Monaco doesn't run in jsdom — swap it for a plain textarea, same as
// CustomJsonInput.test.jsx.
vi.mock("../CodeEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      data-testid="json-editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

// Dataset/Tracing/Simulation source tabs each own a huge amount of
// independent fetch/render logic (project pickers, row browsers, mapping
// UIs). TestPlayground's own contract with them is just: render the right
// one for the active tab, forward the same prop set to each, and delegate
// `ref.runTest()` to whichever one is active. Stub them out so these tests
// stay focused on that wiring rather than re-testing each mode's internals.
const datasetRunTestMock = vi.hoisted(() => vi.fn());
const tracingRunTestMock = vi.hoisted(() => vi.fn());
const simulationRunTestMock = vi.hoisted(() => vi.fn());
const capturedModeProps = vi.hoisted(() => ({
  Dataset: null,
  Tracing: null,
  Simulation: null,
}));

vi.mock("../DatasetTestMode", () => ({
  default: React.forwardRef((props, ref) => {
    capturedModeProps.Dataset = props;
    React.useImperativeHandle(ref, () => ({
      runTest: (tid) => datasetRunTestMock(tid),
    }));
    return (
      <div data-testid="dataset-test-mode">
        <button type="button" onClick={() => props.onReadyChange?.(true, {})}>
          dataset ready
        </button>
      </div>
    );
  }),
}));

vi.mock("../TracingTestMode", () => ({
  default: React.forwardRef((props, ref) => {
    capturedModeProps.Tracing = props;
    React.useImperativeHandle(ref, () => ({
      runTest: (tid) => tracingRunTestMock(tid),
    }));
    return (
      <div data-testid="tracing-test-mode">
        <button type="button" onClick={() => props.onReadyChange?.(true, {})}>
          tracing ready
        </button>
      </div>
    );
  }),
}));

vi.mock("../SimulationTestMode", () => ({
  default: React.forwardRef((props, ref) => {
    capturedModeProps.Simulation = props;
    React.useImperativeHandle(ref, () => ({
      runTest: (tid) => simulationRunTestMock(tid),
    }));
    return (
      <div data-testid="simulation-test-mode">
        <button
          type="button"
          onClick={() => props.onReadyChange?.(true, {})}
        >
          simulation ready
        </button>
      </div>
    );
  }),
}));

const editor = () => screen.getByTestId("json-editor");
const switchTab = (name) =>
  fireEvent.click(screen.getByRole("tab", { name }));
const triggerRun = () =>
  fireEvent.click(screen.getByRole("button", { name: "trigger run" }));

// TestPlayground only exposes `runTest` via an imperative ref — there is no
// visible "Run" button on the Custom tab itself (the run button lives in the
// parent, e.g. EvalDetailPage). This harness mirrors that contract.
function Harness(props) {
  const ref = useRef(null);
  return (
    <>
      <TestPlayground ref={ref} {...props} />
      <button type="button" onClick={() => ref.current?.runTest()}>
        trigger run
      </button>
    </>
  );
}
Harness.propTypes = { props: PropTypes.object };

function renderPlayground(props = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness
        templateId="tpl-1"
        instructions="Check this: {{input}}"
        evalName="Toxicity"
        evalType="llm"
        model="turing_large"
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("TestPlayground", () => {
  beforeEach(() => {
    axiosPostMock.mockReset();
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({ data: { result: { versions: [] } } });
    datasetRunTestMock.mockReset();
    tracingRunTestMock.mockReset();
    simulationRunTestMock.mockReset();
    capturedModeProps.Dataset = null;
    capturedModeProps.Tracing = null;
    capturedModeProps.Simulation = null;
  });

  it("renders all four source tabs with Custom active by default", () => {
    renderPlayground();

    ["Dataset", "Tracing", "Simulation", "Custom"].forEach((name) => {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    });
    expect(screen.getByRole("tab", { name: "Custom" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("posts the expected body and surfaces the result via onTestResult + EvalResultDisplay", async () => {
    axiosPostMock.mockResolvedValueOnce({
      data: {
        status: true,
        result: {
          output: "Passed",
          output_type: "Pass/Fail",
          reason: "Looks fine",
        },
      },
    });
    const onTestResult = vi.fn();
    renderPlayground({ onTestResult });

    // Scaffolded JSON should already contain the "input" key.
    await waitFor(() => expect(editor().value).toContain("input"));

    fireEvent.change(editor(), {
      target: { value: '{"input": "hello world"}' },
    });

    fireEvent.click(screen.getByRole("button", { name: "trigger run" }));

    await waitFor(() =>
      expect(axiosPostMock).toHaveBeenCalledWith("/model-hub/eval-playground/", {
        template_id: "tpl-1",
        model: "turing_large",
        error_localizer: false,
        config: { mapping: { input: "hello world" } },
      }),
    );

    await waitFor(() =>
      expect(onTestResult).toHaveBeenCalledWith(true, {
        output: "Passed",
        output_type: "Pass/Fail",
        reason: "Looks fine",
      }),
    );

    // EvalResultDisplay (real, not mocked) renders the Pass chip + reason.
    expect(await screen.findByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Looks fine")).toBeInTheDocument();
  });

  it("surfaces a failed request instead of swallowing it", async () => {
    axiosPostMock.mockRejectedValueOnce(new Error("Network exploded"));
    const onTestResult = vi.fn();
    renderPlayground({ onTestResult });

    fireEvent.click(screen.getByRole("button", { name: "trigger run" }));

    expect(await screen.findByText("Network exploded")).toBeInTheDocument();
    await waitFor(() =>
      expect(onTestResult).toHaveBeenCalledWith(false, "Network exploded"),
    );
  });

  describe("source tabs: Dataset / Tracing / Simulation", () => {
    it("renders DatasetTestMode on the Dataset tab and forwards the shared prop set", () => {
      renderPlayground({ templateId: "tpl-1", model: "gpt-4o" });
      switchTab("Dataset");

      expect(screen.getByTestId("dataset-test-mode")).toBeInTheDocument();
      expect(screen.queryByTestId("json-editor")).not.toBeInTheDocument();
      expect(capturedModeProps.Dataset).toMatchObject({
        templateId: "tpl-1",
        model: "gpt-4o",
        isComposite: false,
        compositeAdhocConfig: null,
      });
    });

    it("renders TracingTestMode on the Tracing tab with hostsFilter enabled", () => {
      renderPlayground({ templateId: "tpl-1" });
      switchTab("Tracing");

      expect(screen.getByTestId("tracing-test-mode")).toBeInTheDocument();
      expect(capturedModeProps.Tracing).toMatchObject({
        templateId: "tpl-1",
        hostsFilter: true,
      });
    });

    it("renders SimulationTestMode on the Simulation tab", () => {
      renderPlayground({ templateId: "tpl-1" });
      switchTab("Simulation");

      expect(screen.getByTestId("simulation-test-mode")).toBeInTheDocument();
      expect(capturedModeProps.Simulation).toMatchObject({
        templateId: "tpl-1",
      });
    });

    it("delegates ref.runTest() to DatasetTestMode's own runTest when Dataset is active", () => {
      renderPlayground({ templateId: "tpl-1" });
      switchTab("Dataset");
      triggerRun();

      expect(datasetRunTestMock).toHaveBeenCalledWith("tpl-1");
      expect(tracingRunTestMock).not.toHaveBeenCalled();
      expect(simulationRunTestMock).not.toHaveBeenCalled();
    });

    it("delegates ref.runTest() to TracingTestMode's own runTest when Tracing is active", () => {
      renderPlayground({ templateId: "tpl-1" });
      switchTab("Tracing");
      triggerRun();

      expect(tracingRunTestMock).toHaveBeenCalledWith("tpl-1");
      expect(datasetRunTestMock).not.toHaveBeenCalled();
      expect(simulationRunTestMock).not.toHaveBeenCalled();
    });

    it("delegates ref.runTest() to SimulationTestMode's own runTest when Simulation is active", () => {
      renderPlayground({ templateId: "tpl-1" });
      switchTab("Simulation");
      triggerRun();

      expect(simulationRunTestMock).toHaveBeenCalledWith("tpl-1");
      expect(datasetRunTestMock).not.toHaveBeenCalled();
      expect(tracingRunTestMock).not.toHaveBeenCalled();
    });

    it("switching tabs resets the previous result/error and notifies onSourceTabChange", async () => {
      axiosPostMock.mockRejectedValueOnce(new Error("boom"));
      const onSourceTabChange = vi.fn();
      renderPlayground({ onSourceTabChange });

      triggerRun();
      expect(await screen.findByText("boom")).toBeInTheDocument();

      switchTab("Dataset");

      expect(onSourceTabChange).toHaveBeenCalled();
      expect(screen.queryByText("boom")).not.toBeInTheDocument();
    });

    it("bubbles a mode's onReadyChange up to the parent for the active tab only", () => {
      const onReadyChange = vi.fn();
      renderPlayground({ onReadyChange });

      switchTab("Dataset");
      onReadyChange.mockClear();
      fireEvent.click(screen.getByText("dataset ready"));
      expect(onReadyChange).toHaveBeenCalledWith(true, {});
    });
  });

  describe("code eval variable extraction", () => {
    it("uses requiredKeys directly for a system code eval", async () => {
      renderPlayground({
        evalType: "code",
        isSystemEval: true,
        requiredKeys: ["input", "expected"],
        instructions: "",
        code: "",
      });

      await waitFor(() => {
        const parsed = JSON.parse(editor().value);
        expect(Object.keys(parsed).sort()).toEqual(["expected", "input"]);
      });
    });

    it("falls back to the standard trio for user-authored code with no evaluate() signature", async () => {
      renderPlayground({
        evalType: "code",
        isSystemEval: false,
        code: "def not_evaluate():\n    pass",
        codeLanguage: "python",
        instructions: "",
      });

      await waitFor(() => {
        const parsed = JSON.parse(editor().value);
        expect(Object.keys(parsed).sort()).toEqual([
          "expected",
          "input",
          "output",
        ]);
      });
    });

    it("live-parses evaluate() params from user-authored code", async () => {
      renderPlayground({
        evalType: "code",
        isSystemEval: false,
        code: "def evaluate(question, answer, **kwargs):\n    pass",
        codeLanguage: "python",
        instructions: "",
      });

      await waitFor(() => {
        const parsed = JSON.parse(editor().value);
        expect(Object.keys(parsed).sort()).toEqual(["answer", "question"]);
      });
    });

    it("renders schema-defined function params alongside the mapped variables", () => {
      renderPlayground({
        evalType: "code",
        isSystemEval: true,
        requiredKeys: ["input"],
        functionParamsSchema: {
          threshold: { type: "number", default: 0.5 },
        },
      });

      expect(screen.getByText("Threshold")).toBeInTheDocument();
    });
  });

  describe("composite evals", () => {
    it("runs a saved composite via the templateId execute endpoint", async () => {
      axiosPostMock.mockResolvedValueOnce({
        data: {
          result: {
            aggregation_enabled: true,
            aggregate_score: 0.75,
            summary: "3/4 children passed",
          },
        },
      });
      const onTestResult = vi.fn();
      renderPlayground({
        onTestResult,
        isComposite: true,
        compositeAdhocConfig: null,
        templateId: "composite-1",
        requiredKeys: ["input"],
      });

      triggerRun();

      await waitFor(() =>
        expect(axiosPostMock).toHaveBeenCalledWith(
          "/model-hub/eval-templates/composite-1/composite/execute/",
          {
            mapping: {},
            config: {},
            error_localizer: false,
            input_data_types: {},
          },
        ),
      );
      await waitFor(() =>
        expect(onTestResult).toHaveBeenCalledWith(true, {
          output: 0.75,
          reason: "3/4 children passed",
          compositeResult: {
            aggregation_enabled: true,
            aggregate_score: 0.75,
            summary: "3/4 children passed",
          },
        }),
      );
    });

    it("runs an unsaved (adhoc) composite via the execute-adhoc endpoint, merging in the adhoc config", async () => {
      axiosPostMock.mockResolvedValueOnce({
        data: {
          result: {
            aggregation_enabled: false,
            summary: "ran",
          },
        },
      });
      const onTestResult = vi.fn();
      renderPlayground({
        onTestResult,
        isComposite: true,
        templateId: null,
        requiredKeys: ["input"],
        compositeAdhocConfig: {
          child_template_ids: ["c1"],
          aggregation_function: "weighted_avg",
        },
      });

      triggerRun();

      await waitFor(() =>
        expect(axiosPostMock).toHaveBeenCalledWith(
          "/model-hub/eval-templates/composite/execute-adhoc/",
          {
            child_template_ids: ["c1"],
            aggregation_function: "weighted_avg",
            mapping: {},
            config: {},
            error_localizer: false,
            input_data_types: {},
          },
        ),
      );
      await waitFor(() =>
        expect(onTestResult).toHaveBeenCalledWith(true, {
          output: null,
          reason: "ran",
          compositeResult: { aggregation_enabled: false, summary: "ran" },
        }),
      );
    });

    it("without a templateId or an adhoc config, refuses to run and reports the reason", () => {
      const onTestResult = vi.fn();
      renderPlayground({
        onTestResult,
        isComposite: true,
        templateId: null,
        compositeAdhocConfig: null,
      });

      triggerRun();

      expect(onTestResult).toHaveBeenCalledWith(
        false,
        "No template ID - save the eval first",
      );
      expect(axiosPostMock).not.toHaveBeenCalled();
    });
  });
});
