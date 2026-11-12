import React from "react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { buildAddEvalsDraft } from "src/sections/projects/LLMTracing/buildAddEvalsDraft";
import TaskCreatePage from "../TaskCreatePage";

const harness = vi.hoisted(() => ({
  navigate: vi.fn(),
  saveDraft: vi.fn(),
  clearDraft: vi.fn(),
  initialValues: null,
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => harness.navigate,
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useSearchParams: () => [new URLSearchParams()],
}));

vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    project: { createEvalTask: () => "/tracer/eval-task/" },
  },
}));

vi.mock("../hooks/useTaskDraft", () => ({
  useTaskDraft: () => ({
    initialValues: harness.initialValues,
    save: harness.saveDraft,
    clear: harness.clearDraft,
  }),
}));

vi.mock("src/components/resizablePanels/ResizablePanels", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ leftPanel, rightPanel }) => (
    <div>
      {leftPanel}
      {rightPanel}
    </div>
  ),
}));

vi.mock("../components/TaskHeader", () => ({ default: () => null }));

// Stands in for the When to run cards: a click goes through the field's own
// onChange, exactly as TaskSchedulingSection's cards do.
vi.mock("../components/TaskConfigPanel", async () => {
  const { useController } = await import("react-hook-form");
  // eslint-disable-next-line react/prop-types
  function RunTypeCards({ control }) {
    const { field } = useController({ control, name: "runType" });
    return (
      <div>
        <span data-testid="run-type">{field.value}</span>
        <button type="button" onClick={() => field.onChange("historical")}>
          Historical data
        </button>
        <button type="button" onClick={() => field.onChange("continuous")}>
          New incoming data
        </button>
      </div>
    );
  }
  return { default: RunTypeCards };
});

// Stands in for the live preview reporting what its list read found: null
// while it has no answer yet, true for no rows with no filter set, false
// when rows came back.
vi.mock("../components/TaskLivePreview", async () => {
  const ReactModule = await import("react");
  const PREVIEW_ANSWERS = [
    ["Preview still loading", null],
    ["Preview finds data", false],
    ["Preview finds no data", true],
  ];
  // eslint-disable-next-line react/prop-types
  function PreviewDouble({ onTestStateChange }, _ref) {
    return (
      <div>
        {PREVIEW_ANSWERS.map(([label, noDataYet]) => (
          <button
            key={label}
            type="button"
            onClick={() =>
              onTestStateChange({ canTest: false, isTesting: false, noDataYet })
            }
          >
            {label}
          </button>
        ))}
      </div>
    );
  }
  return { default: ReactModule.forwardRef(PreviewDouble) };
});

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({ enqueueSnackbar: vi.fn() }));

// The values Evaluate > Add evaluators hands the create page for a project
// with no filters, read back from the draft buildAddEvalsDraft stores, plus
// the name and evaluator the user adds before creating the task.
const addEvaluatorsDraft = () => {
  const url = buildAddEvalsDraft({ observeId: "project-1", rowType: "spans" });
  const draftId = new URLSearchParams(url.split("?")[1]).get("draft");
  const { values } = JSON.parse(
    window.localStorage.getItem(`task-draft-${draftId}`),
  );
  return {
    ...values,
    name: "First task",
    evalsDetails: [{ id: "eval-config-1" }],
  };
};

const renderPage = () => {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <TaskCreatePage />
    </QueryClientProvider>,
  );
};

describe("Task Create on a project with no data yet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    harness.initialValues = addEvaluatorsDraft();
    axios.post.mockResolvedValue({ data: { result: { id: "task-1" } } });
  });

  it("creates the task on new incoming data instead of an empty past window", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");
    await user.click(
      screen.getByRole("button", { name: "Preview still loading" }),
    );
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");
    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("run-type")).toHaveTextContent("continuous"),
    );

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    const [url, payload] = axios.post.mock.calls[0];
    expect(url).toBe("/tracer/eval-task/");
    expect(payload).toEqual(
      expect.objectContaining({ project: "project-1", run_type: "continuous" }),
    );
    expect(payload).not.toHaveProperty("spans_limit");
  });

  it("defaults a brand-new task with no draft the same way", async () => {
    harness.initialValues = null;
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");
    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("run-type")).toHaveTextContent("continuous"),
    );
  });

  it("keeps Historical data when the user picked it before the preview answered", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("button", { name: "Historical data" }));
    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1]).toEqual(
      expect.objectContaining({ run_type: "historical" }),
    );
  });

  it("keeps Historical data when the project had data and the window is later narrowed to nothing", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      screen.getByRole("button", { name: "Preview finds data" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1]).toEqual(
      expect.objectContaining({ run_type: "historical" }),
    );
  });

  it("keeps Historical data when the user switches back after the default changed", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("run-type")).toHaveTextContent("continuous"),
    );
    await user.click(screen.getByRole("button", { name: "Historical data" }));
    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1]).toEqual(
      expect.objectContaining({ run_type: "historical" }),
    );
  });

  it("keeps a saved Historical data choice and its window when the draft is reopened", async () => {
    const user = userEvent.setup();
    const { unmount } = renderPage();

    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("run-type")).toHaveTextContent("continuous"),
    );
    // The draft records the switch as our default, not as the user's pick.
    expect(harness.saveDraft).toHaveBeenLastCalledWith(
      expect.objectContaining({ runType: "continuous", runTypeChosen: false }),
    );
    await user.click(screen.getByRole("button", { name: "Historical data" }));
    const savedDraft = harness.saveDraft.mock.calls.at(-1)[0];
    expect(savedDraft).toEqual(
      expect.objectContaining({ runType: "historical", runTypeChosen: true }),
    );
    unmount();

    // Reopening the same draft URL hydrates the page from what was saved.
    harness.initialValues = savedDraft;
    renderPage();
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");
    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    const payload = axios.post.mock.calls[0][1];
    expect(payload).toEqual(
      expect.objectContaining({ run_type: "historical", spans_limit: 100000 }),
    );
    expect(payload.filters.date_range).toHaveLength(2);
    expect(payload).not.toHaveProperty("runTypeChosen");
  });

  it("keeps the run type of a draft saved before the choice was recorded", async () => {
    const legacyDraft = addEvaluatorsDraft();
    delete legacyDraft.runTypeChosen;
    harness.initialValues = legacyDraft;
    const user = userEvent.setup();
    renderPage();

    await user.click(
      screen.getByRole("button", { name: "Preview finds no data" }),
    );
    expect(screen.getByTestId("run-type")).toHaveTextContent("historical");

    await user.click(screen.getByRole("button", { name: "Create Task" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post.mock.calls[0][1]).toEqual(
      expect.objectContaining({ run_type: "historical", spans_limit: 100000 }),
    );
  });
});
