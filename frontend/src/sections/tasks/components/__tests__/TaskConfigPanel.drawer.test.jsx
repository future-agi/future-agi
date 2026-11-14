import React from "react";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import TaskConfigPanel from "../TaskConfigPanel";
import TaskPanels from "../task_panels";
import { defaultSamplingPolicy } from "../samplingPolicyForm";
import { productionTheme } from "./samplingContrastTestUtils";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  width: 1280,
  sampling: false,
  project: { data: { source: "sdk" }, isSuccess: true },
  detail: {
    data: {
      id: "template-r8",
      name: "Task drawer evaluation",
      owner: "system",
      eval_type: "llm",
      output_type: "pass_fail",
      instructions: "Grade {{evaluation_result}}",
      config: { model: "template-model" },
    },
    isLoading: false,
    isError: false,
  },
  versions: { data: { versions: [] } },
  update: { mutate: vi.fn(), mutateAsync: vi.fn() },
  createVersion: { mutateAsync: vi.fn() },
  composite: { data: null },
  union: [],
  attributes: {
    data: ["final_status"],
    queryReadState: "complete",
    isFetching: false,
  },
}));
vi.mock("src/utils/axios", () => ({
  readQuery: mocks.get,
  default: { get: mocks.get, post: mocks.post },
  endpoints: {
    project: {
      samplingPreview: () => "/sample-preview/",
      getProjectById: (id) => `/projects/${id}`,
      listProjects: () => "/projects/",
      getSpansForObserveProject: () => "/spans/",
      getObservationSpan: (id) => `/span-details/${id}/`,
      getTracesForObserveProject: () => "/traces/",
      projectSessionList: () => "/sessions/",
      traceSession: "/sessions/",
      getTrace: (id) => `/traces/${id}/`,
    },
  },
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => mocks.project,
}));
vi.mock("src/sections/evals/hooks/useEvalDetail", () => ({
  useEvalDetail: () => mocks.detail,
  useUpdateEval: () => mocks.update,
  evalDetailQuery: vi.fn(),
}));
vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => mocks.versions,
  useCreateEvalVersion: () => mocks.createVersion,
}));
vi.mock("src/sections/evals/hooks/useCompositeEval", () => ({
  useCompositeDetail: () => mocks.composite,
  useExecuteCompositeEvalAdhoc: () => mocks.update,
}));
vi.mock("src/sections/evals/hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenUnionKeys: () => mocks.union,
}));
vi.mock(
  "src/sections/evals/components/useExactEvalAttributeFields",
  async (original) => ({
    ...(await original()),
    useExactEvalAttributeFields: () => mocks.attributes,
  }),
);
vi.mock("src/sections/evals/hooks/useErrorLocalizerPoll", () => ({
  default: () => ({ state: {}, start: vi.fn() }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureAllowed: () => ({ allowed: true, isLoading: false }),
  useFeatureLocked: () => ({ locked: false, isLoading: false }),
  useCapabilities: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: "cloud",
    isCloud: true,
    isOSS: false,
    isEE: false,
  }),
}));
vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/ModalWrapper/ModalWrapper", () => ({
  default: () => null,
}));
vi.mock("../TaskFilterBar", () => ({
  default: ({ toolbarStart }) => toolbarStart || null,
}));
vi.mock("@mui/material", async (original) => ({
  ...(await original()),
  useMediaQuery: () => mocks.width <= 1100,
}));
vi.mock("../TaskSchedulingSection", async (original) => ({
  ...(await original()),
  default: () => null,
}));
vi.mock("../task_sampling_section", async (original) => {
  const actual = await original();
  return {
    ...actual,
    default: (props) => (mocks.sampling ? <actual.default {...props} /> : null),
  };
});
// Only the list data-selection surface is substituted. The production parent,
// Drawer, provider, ConfigFull and TracingTestMode and their props/styles are real.
vi.mock("src/sections/common/EvalPicker/EvalPickerList", () => ({
  default: ({ onSelectEval }) => (
    <button
      onClick={() =>
        onSelectEval({ id: "template-r8", name: "Task drawer evaluation" })
      }
    >
      Choose evaluation
    </button>
  ),
}));
vi.mock("src/sections/common/EvalPicker/EvalPickerCreateNew", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/ModelSelector", () => ({
  default: () => null,
  FAGI_MODEL_VALUES: new Set(),
}));
vi.mock("src/sections/evals/components/LLMPromptEditor", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/InstructionEditor", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/CodeEvalEditor", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/OutputTypeConfig", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/FewShotExamples", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  default: () => null,
  JsonValueTree: () => null,
}));
vi.mock("src/sections/evals/components/SimulationTestMode", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/CreateSimulationPreviewMode", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/TestPlayground", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));

const projectId = "00000000-0000-4000-8000-000000000901";
const row = {
  project_id: projectId,
  trace_id: "trace-r8",
  span_id: "span-r8",
  start_time: "2026-09-01T12:01:02.123456Z",
  observation_type: "SPAN",
  service_name: "test-service",
  _version: "1",
};
let draftForm;
const retainedPolicy = {
  ...defaultSamplingPolicy(),
  coordination: "project",
  panel: "keep-this-panel",
  default: { target_n: 2 },
  cell_quotas: { '["default",[]]': 2 },
};
function TaskForm() {
  const form = useForm({
    defaultValues: {
      name: "Drawer task",
      project: projectId,
      rowType: "spans",
      filters: [],
      evalsDetails: [],
      samplingPolicy: retainedPolicy,
      samplingRate: 50,
      runType: "continuous",
    },
  });
  draftForm = form;
  return (
    <TaskPanels
      rightPanel={
        <input aria-label="Row continuation" defaultValue="cursor-2" />
      }
      leftPanel={
        <TaskConfigPanel
          mode="create"
          projectLocked
          initialProjectName="Project"
          control={form.control}
          getValues={form.getValues}
          setValue={form.setValue}
        />
      }
    />
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.width = 1280;
  mocks.sampling = false;
  mocks.post.mockResolvedValue({
    data: {
      result: {
        strata: [
          {
            stratum: '["default",[]]',
            eligible_units: 20,
            selected_units: 2,
            selected_rows: 4,
          },
        ],
        evaluator_count: 1,
      },
    },
  });
  mocks.get.mockImplementation(async (url) => {
    if (url === `/projects/${projectId}`)
      return { data: { result: { id: projectId, source: "api" } } };
    if (url === "/spans/")
      return {
        data: {
          status: true,
          result: {
            config: [],
            table: [row],
            metadata: { total_rows: 1, has_more: false, next_cursor: null },
          },
        },
      };
    if (url === "/span-details/span-r8/")
      return { data: { status: true, result: { observation_span: row } } };
    throw new Error(`Unexpected GET ${url}`);
  });
});
describe("real task drawer style integration", () => {
  it("preserves the open evaluation drawer and unsaved mapping and retained advanced policy across 1280 → 900 → 1280", async () => {
    mocks.sampling = true;
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const tree = () => (
      <QueryClientProvider client={client}>
        <TaskForm />
      </QueryClientProvider>
    );
    const view = render(tree());
    const user = userEvent.setup();
    const before = structuredClone(draftForm.getValues("samplingPolicy"));
    expect(screen.getByLabelText("Advanced sampling settings")).toBeVisible();
    mocks.width = 900;
    view.rerender(tree());
    expect(screen.getByLabelText("Advanced sampling settings")).toBeVisible();
    expect(draftForm.getValues("samplingPolicy")).toEqual(before);
    mocks.width = 1280;
    view.rerender(tree());
    await user.click(
      screen.getByRole("radio", { name: "A percentage of matching spans" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Use row percentage" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Add Evaluation" }));
    await user.click(
      await screen.findByRole("button", { name: "Choose evaluation" }),
    );
    const mapping = await screen.findByPlaceholderText(
      "Search or type a path (e.g. attributes.input.value)",
    );
    await waitFor(() => expect(mapping).toBeEnabled());
    await user.type(mapping, "unsaved.mapping");
    const drawer = mapping.closest(".MuiDrawer-paper");
    const continuation = screen.getByLabelText("Row continuation");
    mocks.width = 900;
    view.rerender(tree());
    expect(
      screen.getByPlaceholderText(
        "Search or type a path (e.g. attributes.input.value)",
      ),
    ).toBe(mapping);
    expect(mapping).toHaveValue("unsaved.mapping");
    expect(mapping.closest(".MuiDrawer-paper")).toBe(drawer);
    expect(drawer).toBeVisible();
    expect(screen.getByLabelText("Row continuation")).toBe(continuation);
    await user.tab();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(drawer).not.toBeInTheDocument());
    await user.click(
      screen.getByRole("radio", { name: "Pick whole groups of spans" }),
    );
    expect(draftForm.getValues("samplingPolicy")).toEqual(retainedPolicy);
    mocks.width = 1280;
    view.rerender(tree());
    expect(draftForm.getValues("samplingPolicy")).toEqual(retainedPolicy);
    view.unmount();
    client.clear();
  });
  it.each(["light", "dark"])(
    "round8 keeps the portalled mapping and keyboard focus in %s",
    async (mode) => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const view = render(
        <QueryClientProvider client={client}>
          <TaskForm />
        </QueryClientProvider>,
        { theme: productionTheme(mode) },
      );
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "Add Evaluation" }));
      await user.click(
        await screen.findByRole("button", { name: "Choose evaluation" }),
      );
      const input = await screen.findByPlaceholderText(
        "Search or type a path (e.g. attributes.input.value)",
      );
      await waitFor(() => expect(input).toBeEnabled());
      const paper = input.closest(".MuiDrawer-paper");
      expect(paper).toBeTruthy();
      expect(view.container).not.toContainElement(paper);
      await user.type(input, "unsaved.mapping");
      mocks.width = 900;
      view.rerender(
        <QueryClientProvider client={client}>
          <TaskForm />
        </QueryClientProvider>,
      );
      expect(
        screen.getByPlaceholderText(
          "Search or type a path (e.g. attributes.input.value)",
        ),
      ).toBe(input);
      expect(input).toHaveValue("unsaved.mapping");
      expect(paper).toBeVisible();
      // This drawer does not enable hostsFilter. Its mapping-date path is NOT a
      // reachable production control; scheduling dates are tested separately.
      expect(
        within(paper).queryByRole("button", { name: "Past 30D" }),
      ).not.toBeInTheDocument();
      expect(
        within(paper).queryByLabelText("Start Date"),
      ).not.toBeInTheDocument();
      await user.tab();
      expect(input).not.toHaveFocus();
      for (let step = 0; step < 60 && document.activeElement !== input; step++)
        await user.tab();
      expect(input).toHaveFocus();
      view.unmount();
      client.clear();
    },
  );
});
