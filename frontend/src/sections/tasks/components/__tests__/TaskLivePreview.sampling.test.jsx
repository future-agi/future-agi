import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import axios from "src/utils/axios";
import TaskConfigPanel from "../TaskConfigPanel";
import TaskLivePreview from "../TaskLivePreview";
import { defaultSamplingPolicy } from "../samplingPolicyForm";
import { NewTaskValidationSchema } from "../../schema";

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn(), get: vi.fn() },
  readQuery: vi.fn(async () => ({
    data: {
      status: true,
      result: {
        table: [],
        config: [],
        metadata: { total_rows: 0, has_more: false, next_cursor: null },
      },
    },
  })),
  endpoints: {
    project: {
      samplingPreview: () => "/sampling-preview/",
      getSpansForObserveProject: () => "/spans/",
      getTracesForObserveProject: () => "/traces/",
      projectSessionList: () => "/sessions/",
    },
  },
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "sdk" }, isSuccess: true }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: () => null,
}));
vi.mock("src/components/ComplexFilter/FilterErrorBoundary", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/sections/common/EvalPicker", () => ({
  EvalPickerDrawer: () => null,
  serializeEvalConfig: vi.fn(),
}));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));
vi.mock("../TaskFilterBar", () => ({ default: () => null }));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));

const result = {
  strata: [
    {
      stratum: '["default",[]]',
      eligible_units: 2,
      selected_units: 1,
      expected_units: 1,
      selected_rows: 4,
    },
  ],
  evaluator_count: 1,
  expected_evaluations: 4,
  selected_evaluations: 4,
};
let form;
function Harness() {
  form = useForm({
    defaultValues: {
      name: "Sampling task",
      project: "project-1",
      rowType: "spans",
      filters: [],
      runType: "continuous",
      samplingRate: 100,
      spansLimit: "",
      startDate: "2026-09-01T00:00:00Z",
      endDate: "2026-09-02T00:00:00Z",
      samplingPolicy: { ...defaultSamplingPolicy(), default: { target_n: 1 } },
      evalsDetails: [{ id: "eval-1", name: "Quality" }],
    },
  });
  return (
    <>
      <span data-testid="form-dirty">{String(form.formState.isDirty)}</span>
      <TaskConfigPanel
        mode="create"
        control={form.control}
        getValues={form.getValues}
        setValue={form.setValue}
        projectLocked
      />
      <section aria-label="Persistent preview">
        <TaskLivePreview control={form.control} projectId="project-1" />
      </section>
    </>
  );
}
const show = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Harness />
    </QueryClientProvider>,
  );
const preview = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Preview sample" }));
  return screen.findByLabelText("Sampling preview summary");
};
const summaryText = "1 conversations · 4 spans evaluated";

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { result } });
});

describe("persistent sampling preview", () => {
  it("identifies the row browser as filter matching rather than sampled membership", () => {
    show();
    expect(
      within(
        screen.getByRole("region", { name: "Persistent preview" }),
      ).getByText("Filter-matching row — not sampled membership"),
    ).toBeVisible();
  });

  it("points only group picking at Preview sample; a percentage is sampled as new data arrives, or when a historical task runs", async () => {
    show();
    const pane = within(
      screen.getByRole("region", { name: "Persistent preview" }),
    );
    expect(
      pane.getByText(
        "Test on this row checks evaluator mappings only. To see how many groups and rows are picked, use Preview sample in Data to evaluate.",
      ),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Preview sample" }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use row percentage" }),
    );
    expect(form.getValues("samplingPolicy")).toBeNull();
    // The harness schedule is New incoming data.
    expect(form.getValues("runType")).toBe("continuous");
    expect(
      pane.getByText(
        "Test on this row checks evaluator mappings only. Rows are sampled as new data arrives.",
      ),
    ).toBeVisible();
    expect(pane.queryByText(/Preview sample/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Preview sample" }),
    ).not.toBeInTheDocument();
    act(() => form.setValue("runType", "historical"));
    expect(
      await pane.findByText(
        "Test on this row checks evaluator mappings only. Rows are sampled when the task runs.",
      ),
    ).toBeVisible();
    expect(pane.queryByText(/as new data arrives/)).not.toBeInTheDocument();
    expect(pane.queryByText(/Preview sample/)).not.toBeInTheDocument();
  });

  it("mirrors valid counts by the persistent heading, clears policy edits, and never dirties or submits the summary", async () => {
    show();
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    const before = NewTaskValidationSchema().parse(form.getValues());
    const summary = await preview();
    expect(summary).toHaveTextContent(summaryText);
    expect(
      screen.getByLabelText("Sampling population preview"),
    ).toContainElement(summary);
    expect(
      within(
        screen.getByRole("region", { name: "Persistent preview" }),
      ).queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    expect(form.formState.isDirty).toBe(false);
    expect(screen.getByTestId("form-dirty")).toHaveTextContent("false");
    expect(NewTaskValidationSchema().parse(form.getValues())).toEqual(before);
    await userEvent.clear(
      screen.getByRole("spinbutton", { name: "How many conversations?" }),
    );
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    await userEvent.type(
      screen.getByRole("spinbutton", { name: "How many conversations?" }),
      "1",
    );
    await preview();
    // API-only quota changes must invalidate matching counts too; the task
    // screen no longer edits or discards planned quotas.
    const retained = {
      ...form.getValues("samplingPolicy"),
      cell_quotas: { '["default",[]]': 1 },
    };
    await act(async () => form.setValue("samplingPolicy", retained));
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    await preview();
    expect(form.getValues("samplingPolicy")).toEqual(retained);
    expect(
      await screen.findByLabelText("Sampling preview summary"),
    ).toHaveTextContent(summaryText);
    await userEvent.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Use row percentage" }),
    );
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["project", "project-2"],
    ["rowType", "traces"],
    [
      "filters",
      [
        {
          property: "name",
          filterConfig: {
            filterType: "text",
            filterOp: "equals",
            filterValue: "new",
          },
        },
      ],
    ],
    ["runType", "historical"],
    ["startDate", "2026-10-01T00:00:00Z"],
    ["endDate", "2026-10-02T00:00:00Z"],
    ["datePreset", "Custom"],
    ["evalsDetails", [{ id: "eval-2" }]],
  ])("clears the mirrored summary when %s changes", async (field, value) => {
    show();
    expect(await preview()).toHaveTextContent(summaryText);
    act(() => form.setValue(field, value));
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
  });

  it("clears a previous summary during a refresh and after failure", async () => {
    show();
    await preview();
    let reject;
    axios.post.mockReturnValueOnce(
      new Promise((_, fail) => {
        reject = fail;
      }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Preview sample" }),
    );
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    await act(async () => reject(new Error("offline")));
    expect(
      screen.queryByLabelText("Sampling preview summary"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeVisible();
  });

  it("shows zero sampled membership honestly and does not invent missing evaluator counts", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          ...result,
          strata: [
            { ...result.strata[0], selected_units: 0, selected_rows: 0 },
          ],
          selected_evaluations: 0,
          expected_evaluations: 0,
        },
      },
    });
    show();
    expect(await preview()).toHaveTextContent(
      "0 conversations · 0 spans evaluated",
    );
    await userEvent.click(screen.getByText("Evaluation work"));
    expect(
      screen.getByText(
        "Preview: 0 of 2 sessions; 0 spans × 1 evaluator = 0 runs",
      ),
    ).toBeVisible();
    axios.post.mockResolvedValueOnce({
      data: {
        result: {
          ...result,
          evaluator_count: 0,
          selected_evaluations: null,
          expected_evaluations: null,
        },
      },
    });
    expect(await preview()).toHaveTextContent(
      "1 conversations · 4 spans evaluated",
    );
    await userEvent.click(screen.getByText("Evaluation work"));
    expect(
      screen.getByText(
        "Preview: 1 of 2 sessions; 4 spans; select evaluators to estimate runs",
      ),
    ).toBeVisible();
  });
});
