import React, { createRef } from "react";
import PropTypes from "prop-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useForm } from "react-hook-form";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TaskLivePreview from "../TaskLivePreview";

const mocks = vi.hoisted(() => {
  const rows = [
    { trace_id: "trace-a", input: "input A", output: "output A" },
    { trace_id: "trace-b", input: "input B", output: "output B" },
  ];
  return {
    rows,
    list: { rows, total: 2, columns: [] },
    queryClient: { removeQueries: vi.fn() },
    execute: vi.fn(),
  };
});

// Keep list/detail data ready and stable so only evaluation completion is
// deferred. Navigation and form updates still run through the real component.
vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => mocks.queryClient,
  useQuery: ({ queryKey }) => ({
    data:
      queryKey[0] === "task-preview-list"
        ? mocks.list
        : mocks.rows.find((row) => row.trace_id === queryKey[3]),
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ isSuccess: true }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  readQuery: vi.fn(),
  endpoints: {},
}));
vi.mock("src/sections/evals/utils/evalExecution", () => ({
  executeEvalForRow: mocks.execute,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => {
  function Result({ result }) {
    return <pre data-testid="eval-result">{JSON.stringify(result)}</pre>;
  }
  Result.propTypes = { result: PropTypes.object.isRequired };
  return { default: Result };
});

const EVAL = {
  id: "eval-1",
  name: "Preview evaluation",
  template_id: "template-1",
  mapping: { answer: "output" },
};
const success = (label) => ({
  ok: true,
  isComposite: false,
  raw: {
    score: 0.8,
    output: `${label} answer`,
    reason: `${label} explanation`,
  },
});

function deferred() {
  let resolve;
  const promise = new Promise((finish) => {
    resolve = finish;
  });
  return { promise, resolve };
}

function renderPreview(evalsDetails = [EVAL]) {
  const ref = createRef();
  const onTestStateChange = vi.fn();
  let form;
  function Harness({ projectId }) {
    form = useForm({
      defaultValues: {
        filters: [],
        startDate: null,
        endDate: null,
        rowType: "traces",
        evalsDetails,
      },
    });
    return (
      <TaskLivePreview
        ref={ref}
        control={form.control}
        projectId={projectId}
        onTestStateChange={onTestStateChange}
      />
    );
  }
  Harness.propTypes = { projectId: PropTypes.string.isRequired };
  const view = render(<Harness projectId="project-a" />);
  return {
    ...view,
    onTestStateChange,
    start() {
      let run;
      act(() => {
        run = ref.current.runTest();
      });
      return run;
    },
    changeEvals(nextEvals) {
      act(() => form.setValue("evalsDetails", nextEvals));
    },
    changeProject() {
      view.rerender(<Harness projectId="project-b" />);
    },
  };
}

async function complete(pending, run, result) {
  await act(async () => {
    pending.resolve(result);
    await run;
  });
}

// The payload also carries other rows' fields (OBS-SW-111 `noDataYet`), so
// check only the two this row owns, exactly.
function expectTesting(preview, isTesting) {
  expect(preview.onTestStateChange).toHaveBeenLastCalledWith(
    expect.objectContaining({ canTest: true, isTesting }),
  );
}

describe("W10-SETTINGS-TASKS-L02 — preview test ownership", () => {
  beforeEach(() => {
    mocks.execute.mockReset();
    mocks.queryClient.removeQueries.mockClear();
  });
  afterEach(cleanup);

  it("keeps a current-row result's score, answer and explanation", async () => {
    const pending = deferred();
    mocks.execute.mockReturnValueOnce(pending.promise);
    const preview = renderPreview();
    const run = preview.start();
    expectTesting(preview, true);
    expect(mocks.execute).toHaveBeenCalledWith(
      expect.objectContaining({
        currentRow: mocks.rows[0],
        spanDetail: mocks.rows[0],
      }),
    );
    await complete(pending, run, success("A"));
    expect(JSON.parse(screen.getByTestId("eval-result").textContent)).toEqual(
      success("A").raw,
    );
    expectTesting(preview, false);
  });

  it.each([
    ["success", success("A")],
    ["error", { ok: false, errorMessage: "A evaluation failed" }],
  ])(
    "discards row A's late %s after navigating to row B",
    async (_kind, result) => {
      const pending = deferred();
      mocks.execute.mockReturnValueOnce(pending.promise);
      const preview = renderPreview();
      const run = preview.start();
      fireEvent.click(screen.getByRole("button", { name: "Next row" }));
      // String values in the real row-detail table include JSON-style quotes.
      expect(screen.getByText('"input B"')).toBeInTheDocument();
      await complete(pending, run, result);
      expect(screen.queryByTestId("eval-result")).not.toBeInTheDocument();
      expect(screen.queryByText("A evaluation failed")).not.toBeInTheDocument();
      expect(screen.queryByText("Running eval…")).not.toBeInTheDocument();
      expectTesting(preview, false);
    },
  );

  it("lets B start immediately and keeps it running when A completes", async () => {
    const a = deferred();
    const b = deferred();
    mocks.execute.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const preview = renderPreview();
    const runA = preview.start();
    fireEvent.click(screen.getByRole("button", { name: "Next row" }));
    expectTesting(preview, false);
    const runB = preview.start();
    expect(mocks.execute).toHaveBeenLastCalledWith(
      expect.objectContaining({ currentRow: mocks.rows[1] }),
    );
    await complete(a, runA, success("A"));
    expect(screen.queryByTestId("eval-result")).not.toBeInTheDocument();
    expect(screen.getByText("Running eval…")).toBeInTheDocument();
    expectTesting(preview, true);
    await complete(b, runB, success("B"));
    expect(JSON.parse(screen.getByTestId("eval-result").textContent)).toEqual(
      success("B").raw,
    );
    expectTesting(preview, false);
  });

  it("does not resurrect an old attempt after A to B to A navigation", async () => {
    const pending = deferred();
    mocks.execute.mockReturnValueOnce(pending.promise);
    const preview = renderPreview();
    const run = preview.start();
    fireEvent.click(screen.getByRole("button", { name: "Next row" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous row" }));
    expect(screen.getByText('"input A"')).toBeInTheDocument();
    await complete(pending, run, success("old A"));
    expect(screen.queryByTestId("eval-result")).not.toBeInTheDocument();
    expectTesting(preview, false);
  });

  it.each([
    ["mapping edit", { ...EVAL, mapping: { answer: "input" } }],
    ["template replacement", { ...EVAL, template_id: "template-2" }],
  ])(
    "invalidates a pending test on a same-length %s",
    async (_kind, nextEval) => {
      const pending = deferred();
      mocks.execute.mockReturnValueOnce(pending.promise);
      const preview = renderPreview();
      const run = preview.start();
      preview.changeEvals([nextEval]);
      await complete(pending, run, success("old configuration"));
      expect(screen.queryByTestId("eval-result")).not.toBeInTheDocument();
      expectTesting(preview, false);
    },
  );

  it("invalidates a pending test when projects share the same row identity", async () => {
    const pending = deferred();
    mocks.execute.mockReturnValueOnce(pending.promise);
    const preview = renderPreview();
    const run = preview.start();
    preview.changeProject();
    await complete(pending, run, success("old project"));
    expect(screen.queryByTestId("eval-result")).not.toBeInTheDocument();
    expectTesting(preview, false);
  });

  it("keeps the newest attempt when the same row is tested twice", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.execute
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);
    const preview = renderPreview();
    const oldRun = preview.start();
    const newRun = preview.start();
    await complete(newer, newRun, success("new"));
    await complete(older, oldRun, success("old"));
    expect(JSON.parse(screen.getByTestId("eval-result").textContent)).toEqual(
      success("new").raw,
    );
    expectTesting(preview, false);
  });
});
