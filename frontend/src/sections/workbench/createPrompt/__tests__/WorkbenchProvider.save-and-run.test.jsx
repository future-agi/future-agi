import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderWithRouter } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { usePromptWorkbenchContext } from "../WorkbenchContext";
import WorkbenchProvider from "../WorkbenchProvider";

const mocks = vi.hoisted(() => ({
  notify: vi.fn(),
  streamUrl: "ws://test/ws/prompt-stream/",
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: mocks.notify }),
}));
vi.mock("src/utils/logger", () => ({
  default: { error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));
vi.mock("src/sections/workbench/createPrompt/hooks/usePromptStreamUrl", () => ({
  usePromptStreamUrl: () => mocks.streamUrl,
}));
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    develop: {
      runPrompt: {
        getPrompt: (id) => `/prompts/${id}/`,
        addDraftInPrompt: (id) => `/prompts/${id}/add-new-draft/`,
        runTemplatePrompt: (id) => `/prompts/${id}/run_template/`,
        getStatus: (id) => `/prompts/${id}/get-run-status/`,
        compareVersions: (id) => `/prompts/${id}/compare/`,
      },
    },
  },
}));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const config = {
  messages: [{ role: "user", content: [{ type: "text", text: "hello" }] }],
  configuration: { model: "original", tools: [], output_format: "string" },
};
const savedVersion = (version, isDraft) => ({
  template_version: version,
  is_draft: isDraft,
  prompt_config_snapshot: config,
  output: ["previous output"],
});
let context;
let navigate;
let sockets;
function Capture() {
  context = usePromptWorkbenchContext();
  navigate = useNavigate();
  return null;
}
async function mount(isDraft = true, metadata = undefined) {
  axios.get.mockResolvedValue({
    data: {
      name: "Prompt",
      version: "v1",
      is_draft: isDraft,
      prompt_config: [config],
      output: ["previous output"],
      metadata,
    },
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = renderWithRouter(
    <QueryClientProvider client={client}>
      <Routes>
        <Route
          path="/dashboard/workbench/create/:id"
          element={
            <WorkbenchProvider>
              <Capture />
            </WorkbenchProvider>
          }
        />
      </Routes>
    </QueryClientProvider>,
    { route: "/dashboard/workbench/create/prompt-1" },
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(context.modelConfig[0].model).toBe("original");
  return view;
}
function editModel(model, index = 0) {
  act(() =>
    context.setModelConfigByIndex(index, (previous) => ({
      ...previous,
      model,
    })),
  );
}
async function startRun(index) {
  let run;
  await act(async () => {
    run = context.saveAndRun(index);
  });
  return { run };
}
function sentVersions() {
  sockets.forEach((socket) => socket.onopen());
  return sockets.map(
    (socket) => JSON.parse(socket.send.mock.calls[0][0]).version,
  );
}

describe("Unit: Workbench save-before-run", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mocks.streamUrl = "ws://test/ws/prompt-stream/";
    sockets = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        send = vi.fn();
        close = vi.fn();
        constructor() {
          sockets.push(this);
        }
      },
    );
  });
  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it.each(["default", "fallback", "available_models"])(
    "retains %s pricing source when loading saved output",
    async (source) => {
      await mount(true, [{ cost: { total_cost: 0, pricing_source: source } }]);
      expect(context.results[0].output[0].metadata).toMatchObject({
        cost: 0,
        pricingSource: source,
      });
    },
  );

  it("retains estimated costs when adding a saved comparison", async () => {
    await mount();
    act(() =>
      context.applyCompare([
        {
          ...savedVersion("v2", false),
          metadata: [{ cost: { total_cost: 0.2, pricing_source: "fallback" } }],
        },
      ]),
    );
    expect(context.results[1].output[0].metadata).toMatchObject({
      cost: 0.2,
      pricingSource: "fallback",
    });
  });

  it.each([0, undefined])(
    "flushes the click-time model before constructing a socket (index %s)",
    async (index) => {
      await mount();
      const save = deferred();
      axios.post.mockReturnValue(save.promise);
      // The debounce captures mutateAsync before this edit renders. Real TanStack
      // must use its observer's updated mutationFn, not the original model closure.
      editModel("selected");
      const { run } = await startRun(index);
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(axios.post.mock.calls[0][1]).toMatchObject({
        version: "v1",
        is_run: false,
        prompt_config: [{ configuration: { model: "selected" } }],
      });
      expect(sockets).toHaveLength(0);
      expect(context.promptGeneratingStatus).toEqual([true]);
      await act(async () => {
        save.resolve({});
        await run;
      });
      expect(sentVersions()).toEqual(["v1"]);
      expect(context.selectedVersions[0].isDraft).toBe(false);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      expect(axios.post).toHaveBeenCalledTimes(1);
    },
  );

  it("awaits a save whose debounce timer already fired", async () => {
    await mount();
    const save = deferred();
    axios.post.mockReturnValue(save.promise);
    editModel("selected");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    const { run } = await startRun(0);
    expect(sockets).toHaveLength(0);
    expect(axios.post).toHaveBeenCalledTimes(1);
    await act(async () => {
      save.resolve({});
      await run;
    });
    expect(sentVersions()).toEqual(["v1"]);
  });

  it("serializes an older in-flight save before the final model save", async () => {
    await mount();
    const first = deferred();
    const last = deferred();
    axios.post
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(last.promise);
    editModel("A");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    editModel("B");
    const { run } = await startRun();
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(sockets).toHaveLength(0);
    await act(async () => {
      first.resolve({});
    });
    expect(
      axios.post.mock.calls.map(
        ([, payload]) => payload.prompt_config[0].configuration.model,
      ),
    ).toEqual(["A", "B"]);
    expect(sockets).toHaveLength(0);
    await act(async () => {
      last.resolve({});
      await run;
    });
    expect(sentVersions()).toEqual(["v1"]);
  });

  it.each([true, false])(
    "fails closed on save rejection (socket available: %s)",
    async (hasSocket) => {
      await mount();
      mocks.streamUrl = hasSocket ? "ws://test/ws/prompt-stream/" : null;
      const save = deferred();
      axios.post.mockReturnValue(save.promise);
      editModel("selected");
      const { run } = await startRun(0);
      await act(async () => {
        save.reject(new Error("Save unavailable"));
        await run;
      });
      expect(sockets).toHaveLength(0);
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(axios.post.mock.calls[0][1].is_run).toBe(false);
      expect(context.promptGeneratingStatus).toEqual([false]);
      expect(context.selectedVersions[0].isDraft).toBe(true);
      expect(context.results[0].output[0].text).toBe("previous output");
      expect(mocks.notify).toHaveBeenCalledWith("Save unavailable", {
        variant: "error",
      });
    },
  );

  it("retries a failed save on the next Run, then dispatches", async () => {
    await mount();
    axios.post.mockRejectedValueOnce(new Error("Save unavailable"));
    editModel("selected");
    await act(async () => {
      await context.saveAndRun(0);
    });
    expect(sockets).toHaveLength(0);
    expect(mocks.notify).toHaveBeenCalledWith("Save unavailable", {
      variant: "error",
    });
    axios.post.mockResolvedValueOnce({});
    await act(async () => {
      await context.saveAndRun(0);
    });
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post.mock.calls[1][1]).toMatchObject({
      is_run: false,
      prompt_config: [{ configuration: { model: "selected" } }],
    });
    expect(sentVersions()).toEqual(["v1"]);
  });

  it("drops a failed autosave once its column holds a committed version", async () => {
    await mount();
    axios.post.mockRejectedValueOnce(new Error("Save unavailable"));
    editModel("selected");
    await act(async () => {
      await context.saveAndRun(0);
    });
    // Restoring a committed version from history replaces the draft column.
    act(() =>
      context.setSelectedVersionByIndex(0, (previous) => ({
        ...previous,
        isDraft: false,
      })),
    );
    await act(async () => {
      await context.saveAndRun(0);
    });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(sentVersions()).toEqual(["v1"]);
  });

  it("Stop during the save wait cancels the pending run", async () => {
    await mount();
    const save = deferred();
    axios.post.mockReturnValue(save.promise);
    editModel("selected");
    const { run } = await startRun(0);
    // Both Stop Generating buttons close the column's run and compare sockets.
    act(() => {
      context.closeSocketByIndex("compare-0");
      context.closeSocketByIndex("run-0");
    });
    await act(async () => {
      save.resolve({});
      await run;
    });
    expect(sockets).toHaveLength(0);
  });

  it("saves an edit typed during the save wait before dispatching", async () => {
    await mount();
    const first = deferred();
    axios.post.mockReturnValueOnce(first.promise).mockResolvedValueOnce({});
    editModel("selected");
    const { run } = await startRun(0);
    editModel("edited-during-wait");
    await act(async () => {
      first.resolve({});
      await run;
    });
    expect(
      axios.post.mock.calls.map(
        ([, payload]) => payload.prompt_config[0].configuration.model,
      ),
    ).toEqual(["selected", "edited-during-wait"]);
    expect(sentVersions()).toEqual(["v1"]);
  });

  it("keeps flushing edits typed during each save until none is left", async () => {
    await mount();
    const saves = Array.from({ length: 7 }, deferred);
    axios.post.mockImplementation(
      () => saves[axios.post.mock.calls.length - 1].promise,
    );
    editModel("edit-0");
    const { run } = await startRun(0);
    for (let edit = 1; edit < saves.length; edit += 1) {
      editModel(`edit-${edit}`);
      await act(async () => {
        saves[edit - 1].resolve({});
      });
    }
    expect(sockets).toHaveLength(0);
    await act(async () => {
      saves[saves.length - 1].resolve({});
      await run;
    });
    expect(
      axios.post.mock.calls.map(
        ([, payload]) => payload.prompt_config[0].configuration.model,
      ),
    ).toEqual(saves.map((_, edit) => `edit-${edit}`));
    expect(sentVersions()).toEqual(["v1"]);
  });

  it("Stop on one column during Run all clears every column's spinner", async () => {
    await mount();
    act(() => context.applyCompare([savedVersion("v2", true)]));
    const save = deferred();
    axios.post.mockReturnValue(save.promise);
    editModel("selected", 0);
    const { run } = await startRun();
    expect(context.promptGeneratingStatus).toEqual([true, true]);
    act(() => context.closeSocketByIndex("compare-0"));
    await act(async () => {
      save.resolve({});
      await run;
    });
    expect(sockets).toHaveLength(0);
    expect(context.promptGeneratingStatus).toEqual([false, false]);
  });

  it("Run, Stop, Run during one save dispatches once and keeps the spinner", async () => {
    await mount();
    const save = deferred();
    axios.post.mockReturnValue(save.promise);
    editModel("selected");
    const { run: stopped } = await startRun(0);
    act(() => {
      context.closeSocketByIndex("compare-0");
      context.closeSocketByIndex("run-0");
    });
    const { run: rerun } = await startRun(0);
    await act(async () => {
      save.resolve({});
      await Promise.all([stopped, rerun]);
    });
    expect(sentVersions()).toEqual(["v1"]);
    expect(context.promptGeneratingStatus).toEqual([true]);
  });

  it("a column Run after Stop on Run all dispatches only that column", async () => {
    await mount();
    act(() => context.applyCompare([savedVersion("v2", true)]));
    const save = deferred();
    axios.post.mockReturnValue(save.promise);
    editModel("selected", 0);
    const { run: all } = await startRun();
    act(() => {
      [0, 1].forEach((column) => {
        context.closeSocketByIndex(`compare-${column}`);
        context.closeSocketByIndex(`run-${column}`);
      });
    });
    const { run: single } = await startRun(0);
    await act(async () => {
      save.resolve({});
      await Promise.all([all, single]);
    });
    expect(sentVersions()).toEqual(["v1"]);
    expect(context.promptGeneratingStatus).toEqual([true, false]);
  });

  it("does not run a column that moved during the save wait", async () => {
    await mount();
    act(() => context.applyCompare([savedVersion("v2", true)]));
    const save = deferred();
    axios.post.mockReturnValue(save.promise);
    editModel("selected", 1);
    const { run } = await startRun(1);
    act(() => context.removeFromCompare(0));
    await act(async () => {
      save.resolve({});
      await run;
    });
    expect(sockets).toHaveLength(0);
    expect(context.selectedVersions.map(({ version }) => version)).toEqual([
      "v2",
    ]);
    expect(context.promptGeneratingStatus).toEqual([false]);
    expect(mocks.notify).toHaveBeenCalledWith(
      "The prompt changed while saving. Run it again.",
      { variant: "info" },
    );
  });

  it("does not Run all after a sibling column changed version during the save", async () => {
    await mount();
    act(() => context.applyCompare([savedVersion("v2", false)]));
    const save = deferred();
    const addDraft = deferred();
    axios.post.mockImplementation((url) =>
      url.endsWith("/add-new-draft/") ? addDraft.promise : save.promise,
    );
    editModel("selected", 0);
    const { run } = await startRun();
    // Editing the committed sibling starts a new draft; its version is null
    // until the draft is created.
    editModel("edited", 1);
    expect(context.selectedVersions[1]).toMatchObject({
      isDraft: true,
      version: null,
    });
    await act(async () => {
      save.resolve({});
      await run;
    });
    expect(sockets).toHaveLength(0);
    expect(context.promptGeneratingStatus).toEqual([false, false]);
    expect(mocks.notify).toHaveBeenCalledWith(
      "The prompt changed while saving. Run it again.",
      { variant: "info" },
    );
  });

  it("toasts a rate-limited save that the app handler leaves silent", async () => {
    await mount();
    const error = Object.assign(new Error("Rate limit reached"), {
      result: { detail: "Too many requests" },
      statusCode: 429,
    });
    axios.post.mockRejectedValueOnce(error);
    editModel("selected");
    await act(async () => {
      await context.saveAndRun(0);
    });
    expect(sockets).toHaveLength(0);
    expect(mocks.notify).toHaveBeenCalledWith("Rate limit reached", {
      variant: "error",
    });
  });

  it("leaves HTTP error toasts to the app's mutation handler", async () => {
    await mount();
    const error = Object.assign(new Error("Request failed"), {
      result: { detail: "Bad payload" },
    });
    axios.post.mockRejectedValueOnce(error);
    editModel("selected");
    await act(async () => {
      await context.saveAndRun(0);
    });
    expect(sockets).toHaveLength(0);
    expect(context.promptGeneratingStatus).toEqual([false]);
    expect(mocks.notify).not.toHaveBeenCalled();
  });

  it.each([0, undefined])(
    "runs a clean committed version without saving (index %s)",
    async (index) => {
      await mount(false);
      await act(async () => {
        await context.saveAndRun(index);
      });
      expect(axios.post).not.toHaveBeenCalled();
      expect(sentVersions()).toEqual(["v1"]);
    },
  );

  it.each([true, false])(
    "does not dispatch or overwrite the newly navigated prompt (save succeeds: %s)",
    async (succeeds) => {
      await mount();
      const save = deferred();
      axios.post.mockReturnValue(save.promise);
      editModel("selected");
      const { run } = await startRun(0);
      axios.get.mockResolvedValue({
        data: {
          name: "Next prompt",
          version: "v9",
          is_draft: true,
          prompt_config: [
            {
              ...config,
              configuration: { ...config.configuration, model: "next-model" },
            },
          ],
        },
      });
      await act(async () => {
        navigate("/dashboard/workbench/create/prompt-2");
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(context.promptName).toBe("Next prompt");
      expect(context.modelConfig[0].model).toBe("next-model");
      // A new prompt's own loading state must not be cleared by the old save.
      act(() => context.setLoadingStatus([true]));
      await act(async () => {
        if (succeeds) save.resolve({});
        else save.reject(new Error("Old save failed"));
        await run;
      });
      expect(sockets).toHaveLength(0);
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(context.selectedVersions[0]).toMatchObject({
        version: "v9",
        isDraft: true,
      });
      expect(context.modelConfig[0].model).toBe("next-model");
      expect(context.promptGeneratingStatus).toEqual([true]);
      expect(mocks.notify).not.toHaveBeenCalled();
    },
  );

  it.each([true, false])(
    "does not dispatch after unmount (save succeeds: %s)",
    async (succeeds) => {
      const view = await mount();
      const save = deferred();
      axios.post.mockReturnValue(save.promise);
      editModel("selected");
      const { run } = await startRun();
      view.unmount();
      await act(async () => {
        if (succeeds) save.resolve({});
        else save.reject(new Error("Old save failed"));
        await run;
      });
      expect(sockets).toHaveLength(0);
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(mocks.notify).not.toHaveBeenCalled();
    },
  );

  it.each([true, false])(
    "awaits every compare target before dispatch (last save succeeds: %s)",
    async (succeeds) => {
      await mount();
      act(() => context.applyCompare([savedVersion("v2", true)]));
      const first = deferred();
      const last = deferred();
      axios.post
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(last.promise);
      editModel("first", 0);
      editModel("last", 1);
      const { run } = await startRun();
      expect(
        axios.post.mock.calls.map(([, payload]) => payload.version),
      ).toEqual(["v1", "v2"]);
      expect(context.promptGeneratingStatus).toEqual([true, true]);
      expect(sockets).toHaveLength(0);
      await act(async () => {
        first.resolve({});
      });
      expect(sockets).toHaveLength(0);
      await act(async () => {
        if (succeeds) last.resolve({});
        else last.reject(new Error("Second save failed"));
        await run;
      });
      if (succeeds) {
        expect(sentVersions()).toEqual(["v1", "v2"]);
      } else {
        expect(sockets).toHaveLength(0);
        expect(context.promptGeneratingStatus).toEqual([false, false]);
        expect(mocks.notify).toHaveBeenCalledWith("Second save failed", {
          variant: "error",
        });
      }
    },
  );

  it("flushes only the requested compare column", async () => {
    await mount(false);
    act(() => context.applyCompare([savedVersion("v2", true)]));
    editModel("pending-other-column", 1);
    await act(async () => {
      await context.saveAndRun(0);
    });
    expect(axios.post).not.toHaveBeenCalled();
    expect(sentVersions()).toEqual(["v1"]);
  });
});
