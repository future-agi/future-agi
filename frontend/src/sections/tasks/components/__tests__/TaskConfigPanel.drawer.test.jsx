import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  render,
  renderHook,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import { useCompositeChildrenUnionKeys } from "src/sections/evals/hooks/useCompositeChildrenKeys";
import { getDefaultTaskValues } from "../../schema";
import TaskConfigPanel from "../TaskConfigPanel";
import TaskPanels from "../task_panels";
import { defaultSamplingPolicy } from "../samplingPolicyForm";
import { productionTheme } from "./samplingContrastTestUtils";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  parentDetail: null,
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
  attributes: {
    data: ["final_status"],
    queryReadState: "complete",
    isFetching: false,
  },
}));
vi.mock("src/utils/axios", () => ({
  readQuery: mocks.get,
  default: { get: mocks.get, post: mocks.post, patch: mocks.patch },
  endpoints: {
    develop: { eval: {
      getEvalDetail: (id) => `/evals/${id}/`,
      getCompositeDetail: (id) => `/composites/${id}/`,
    } },
    project: {
      createEvalTaskConfig: () => "/configs/",
      updateEvalTaskConfig: (id) => `/configs/${id}/`,
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
vi.mock("src/sections/evals/hooks/useEvalDetail", async (original) => ({
  ...(await original()),
  useEvalDetail: () => mocks.parentDetail ?? mocks.detail,
  useUpdateEval: () => mocks.update,
}));
vi.mock("src/sections/evals/hooks/useEvalVersions", () => ({
  useEvalVersions: () => mocks.versions,
  useCreateEvalVersion: () => mocks.createVersion,
}));
vi.mock("src/sections/evals/hooks/useCompositeEval", async (original) => ({
  ...(await original()),
  useExecuteCompositeEvalAdhoc: () => mocks.update,
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
  span_attributes: { input: 1, output: 0 },
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
function TaskForm({ savedTask, runType = "continuous" }) {
  const form = useForm({
    defaultValues: savedTask
      ? getDefaultTaskValues(savedTask)
      : {
          name: "Drawer task",
          project: projectId,
          rowType: "spans",
          filters: [],
          evalsDetails: [],
          samplingPolicy: retainedPolicy,
          samplingRate: 50,
          runType,
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
          mode={savedTask ? "edit" : "create"}
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
TaskForm.propTypes = {
  savedTask: PropTypes.object,
  runType: PropTypes.string,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.width = 1280;
  mocks.sampling = false;
  mocks.parentDetail = null;
  mocks.composite.data = null;
  mocks.composite.wait = null;
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

describe("historical task composite mappings", () => {
  const pathPlaceholder = "Search or type a path (e.g. attributes.input.value)";
  const bindingId = "binding-10440";

  const newClient = () => new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  function mountTask(savedTask, client = newClient()) {
    return render(
      <QueryClientProvider client={client}>
        <TaskForm runType="historical" savedTask={savedTask} />
      </QueryClientProvider>,
    );
  }

  function compositeFixture(children) {
    mocks.parentDetail = {
      data: {
        id: "template-r8",
        name: "Weighted votes",
        owner: "user",
        eval_type: "code",
        template_type: "composite",
        config: {},
      },
    };
    mocks.composite.data = {
      aggregation_function: "weighted_avg",
      children: children.map(({ id, weight, params }) => ({
        config: { params },
        child_id: id,
        child_name: id,
        eval_type: "code",
        required_keys: [],
        weight,
      })),
    };
    const get = mocks.get.getMockImplementation();
    mocks.get.mockImplementation(async (url, ...args) => {
      if (url === "/composites/template-r8/") {
        await mocks.composite.wait;
        return { data: { result: structuredClone(mocks.composite.data) } };
      }
      const child = children.find(({ id }) => url === `/evals/${id}/`);
      if (!child) return get(url, ...args);
      if (child.wait) await child.wait;
      return {
        data: {
          result: {
            id: child.id,
            owner: "user",
            eval_type: "code",
            required_keys: [],
            config: { code: child.code, language: child.language || "python" },
          },
        },
      };
    });
    mocks.post.mockResolvedValue({ data: { result: { id: bindingId } } });
    mocks.patch.mockResolvedValue({ data: { result: { id: bindingId } } });
  }

  async function selectPath(user, input, path) {
    await user.click(input);
    await user.click(await screen.findByRole("option", { name: path, exact: true }));
    expect(input).toHaveValue(path);
  }

  async function chooseComposite(user) {
    await user.click(screen.getByRole("button", { name: "Add Evaluation" }));
    const choose = await screen.findByRole("button", {
      name: "Choose evaluation",
    });
    const drawer = within(choose.closest(".MuiDrawer-paper"));
    await user.click(choose);
    return drawer;
  }

  it("keeps declared-key precedence and ignores code signatures without the task opt-in", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: Infinity } },
    });
    const details = [
      {
        owner: "user",
        eval_type: "code",
        required_keys: [],
        config: { required_keys: ["hidden"], code: "def evaluate(vote): return vote" },
      },
      { required_keys: ["top"], config: { required_keys: ["hidden"] } },
      { config: { required_keys: ["snake"], requiredKeys: ["hidden"] } },
      { config: { requiredKeys: ["camel"] } },
    ];
    const children = details.map((detail, i) => {
      const id = `child-${i}`;
      client.setQueryData(["evals", "detail", id], detail);
      return { child_id: id };
    });
    const { result } = renderHook(() => useCompositeChildrenUnionKeys(children), {
      wrapper: ({ children: content }) => (
        <QueryClientProvider client={client}>{content}</QueryClientProvider>
      ),
    });
    expect(result.current).toEqual(["top", "snake", "camel"]);
  });

  it("saves both weighted child inputs and restores them from task detail on edit", async () => {
    let releaseChild;
    const wait = new Promise((resolve) => {
      releaseChild = resolve;
    });
    compositeFixture([
      {
        id: "first",
        weight: 1,
        code: "def evaluate(first_vote, **kwargs):\n    return float(first_vote)",
      },
      {
        id: "second",
        weight: 3,
        code: "def evaluate(second_vote, **kwargs):\n    return float(second_vote)",
        wait,
      },
    ]);
    const user = userEvent.setup();
    const firstView = mountTask();
    const drawer = await chooseComposite(user);
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/evals/second/"),
    );
    expect(
      drawer.queryByRole("button", { name: "Add Evaluation" }),
    ).not.toBeInTheDocument();
    releaseChild();

    await drawer.findByPlaceholderText("Search columns or values...");
    const inputs = await drawer.findAllByPlaceholderText(pathPlaceholder);
    expect(inputs).toHaveLength(2);
    const add = drawer.getByRole("button", { name: "Add Evaluation" });
    await waitFor(() => expect(inputs[0]).toBeEnabled());
    await selectPath(user, inputs[0], "input");
    expect(add).toBeDisabled();
    await selectPath(user, inputs[1], "output");
    await waitFor(() => expect(add).toBeEnabled());
    await user.click(add);
    const mapping = { first_vote: "input", second_vote: "output" };
    const expectedBinding = expect.objectContaining({
      eval_template: "template-r8",
      mapping,
      config: expect.objectContaining({ mapping }),
    });
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith("/configs/", expectedBinding),
    );
    const [, created] = mocks.post.mock.calls.find(
      ([url]) => url === "/configs/",
    );
    firstView.unmount();

    // Start a new form and query cache with the task-detail API shape, not
    // the picker object that was still in memory when the binding was added.
    mountTask({
      name: "Historical composite",
      project_id: projectId,
      run_type: "historical",
      row_type: "spans",
      sampling_rate: 100,
      filters_applied: {},
      evals_applied: [
        {
          id: bindingId,
          template_id: created.eval_template,
          templateType: "composite",
          name: created.name,
          // A child present on the old binding was subsequently removed.
          mapping: { ...created.mapping, removed_vote: "project_id" },
          config: { ...created.config, mapping: { ...created.mapping, removed_vote: "project_id" } },
        },
      ],
    });
    await user.click(screen.getByRole("button", { name: "Edit evaluation" }));
    await screen.findByPlaceholderText("Search columns or values...");
    const restored = await screen.findAllByPlaceholderText(pathPlaceholder);
    expect(restored.map((input) => input.value)).toEqual(["input", "output"]);
    const update = screen.getByRole("button", { name: "Update Evaluation" });
    await waitFor(() => expect(update).toBeEnabled());
    await user.click(update);
    await waitFor(() =>
      expect(mocks.patch).toHaveBeenCalledWith(
        `/configs/${bindingId}/`,
        expectedBinding,
      ),
    );
    const [, updated] = mocks.patch.mock.calls[0];
    expect(updated.mapping).toEqual(mapping);
    expect(updated.config.mapping).toEqual(mapping);
  });

  it("uses executable entry points, optional overrides and each child's own static params", async () => {
    compositeFixture([
      {
        id: "configured", weight: 1, params: { vote: 1, zero: 0, enabled: false },
        code: "# def evaluate(wrong):\ndef main(vote, zero, enabled, cutoff=0.5, **kwargs):\n    return vote",
      },
      {
        id: "mapped", weight: 1,
        code: "# def evaluate(stale):\ndef main(vote, **kwargs):\n    return vote",
      },
      {
        id: "js", weight: 1, language: "javascript",
        code: "function main({ obsolete }) { return obsolete; }\nfunction evaluate({ vote, threshold = 0.5 }) { return vote >= threshold; }",
      },
    ]);
    const view = mountTask();
    const user = userEvent.setup();
    const drawer = await chooseComposite(user);
    await drawer.findByPlaceholderText("Search columns or values...");
    const inputs = await drawer.findAllByPlaceholderText(pathPlaceholder);
    expect(inputs).toHaveLength(3);
    expect(drawer.getByText("cutoff (optional)")).toBeVisible();
    expect(drawer.getByText("threshold (optional)")).toBeVisible();
    const add = drawer.getByRole("button", { name: "Add Evaluation" });
    expect(add).toBeDisabled();
    const vote = within(drawer.getByText("vote").parentElement.parentElement).getByRole("combobox");
    await waitFor(() => expect(vote).toBeEnabled());
    await selectPath(user, vote, "input");
    await waitFor(() => expect(add).toBeEnabled());
    await user.click(add);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/configs/", expect.any(Object)));
    const [, body] = mocks.post.mock.calls.find(([url]) => url === "/configs/");
    expect(body.mapping).toEqual({ vote: "input" });
    expect(body.config.mapping).toEqual(body.mapping);
    view.unmount();

    mountTask({
      project_id: projectId, run_type: "historical", row_type: "spans",
      filters_applied: {}, sampling_rate: 100,
      evals_applied: [{ ...body, id: bindingId, template_id: body.eval_template }],
    });
    await user.click(screen.getByRole("button", { name: "Edit evaluation" }));
    await screen.findByPlaceholderText("Search columns or values...");
    const threshold = within(screen.getByText("threshold (optional)").parentElement.parentElement).getByRole("combobox");
    expect(threshold).toHaveValue("");
    const update = screen.getByRole("button", { name: "Update Evaluation" });
    await waitFor(() => expect(update).toBeEnabled());
    await selectPath(user, threshold, "output");
    await user.click(update);
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledWith(`/configs/${bindingId}/`, expect.any(Object)));
    const [, edited] = mocks.patch.mock.calls[0];
    expect(edited.mapping).toEqual({ vote: "input", threshold: "output" });
    expect(edited.config.mapping).toEqual(edited.mapping);
  });

  it("does not mistake an unrecognized entry point for a verified input-free one", async () => {
    compositeFixture([{ id: "unknown", weight: 1, code: "# def evaluate(): pass" }]);
    mountTask();
    const user = userEvent.setup();
    const drawer = await chooseComposite(user);
    expect(await drawer.findByText(/Cannot determine child inputs/)).toBeVisible();
    expect(drawer.getByRole("button", { name: "Add Evaluation" })).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalledWith("/configs/", expect.anything());
  });

  it("keeps edits during warm schema refresh and retry, then saves only current keys", async () => {
    const child = { id: "changing", weight: 1, code: "def evaluate(vote, old): return vote" };
    compositeFixture([child]);
    const client = newClient();
    mountTask(undefined, client);
    const user = userEvent.setup();
    const drawer = await chooseComposite(user);
    await drawer.findByPlaceholderText("Search columns or values...");
    const [vote, old] = await drawer.findAllByPlaceholderText(pathPlaceholder);
    await waitFor(() => expect(vote).toBeEnabled());
    await selectPath(user, vote, "output");
    await selectPath(user, old, "input");
    const add = drawer.getByRole("button", { name: "Add Evaluation" });
    await waitFor(() => expect(add).toBeEnabled());

    let finishParent;
    mocks.composite.wait = new Promise((resolve) => { finishParent = resolve; });
    act(() => { void client.invalidateQueries({ queryKey: ["evals", "composite", "template-r8"] }); });
    await waitFor(() => expect(add).toBeDisabled());
    expect(vote).toHaveValue("output");
    finishParent();
    await waitFor(() => expect(add).toBeEnabled());

    let failChild;
    child.wait = new Promise((_, reject) => { failChild = reject; });
    act(() => { void client.invalidateQueries({ queryKey: ["evals", "detail", child.id] }); });
    await waitFor(() => expect(add).toBeDisabled());
    await user.click(add);
    expect(mocks.post).not.toHaveBeenCalledWith("/configs/", expect.anything());
    failChild(new Error("child unavailable"));
    const retry = await drawer.findByRole("button", { name: "Retry loading inputs" });
    expect(add).toBeDisabled();
    expect(vote).toHaveValue("output");
    child.wait = null;
    child.code = "def evaluate(vote, replacement): return vote";
    await user.click(retry);
    await drawer.findByText("replacement");
    expect(drawer.queryByText("old")).not.toBeInTheDocument();
    expect(vote).toHaveValue("output");
    expect(add).toBeDisabled();
    const [, replacement] = drawer.getAllByPlaceholderText(pathPlaceholder);
    await selectPath(user, replacement, "input");
    await waitFor(() => expect(add).toBeEnabled());
    await user.click(add);
    await waitFor(() => expect(mocks.post).toHaveBeenCalledWith("/configs/", expect.any(Object)));
    const [, body] = mocks.post.mock.calls.find(([url]) => url === "/configs/");
    expect(body.mapping).toEqual({ vote: "output", replacement: "input" });
    expect(body.config.mapping).toEqual(body.mapping);
  });

  it("allows a resolved input-free child without inventing a mapping", async () => {
    compositeFixture([
      { id: "constant", weight: 1, code: "def evaluate(**kwargs):\n    return 1" },
    ]);
    const user = userEvent.setup();
    mountTask();
    const drawer = await chooseComposite(user);
    const add = await drawer.findByRole("button", { name: "Add Evaluation" });
    await waitFor(() => expect(add).toBeEnabled());
    expect(drawer.queryByPlaceholderText(pathPlaceholder)).not.toBeInTheDocument();
    await user.click(add);
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/configs/",
        expect.objectContaining({ mapping: {} }),
      ),
    );
  });
});
