import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { resetAllStores } from "src/utils/zustand";
import RerunWithChangesDialog from "../RerunWithChangesDialog";

const state = vi.hoisted(() => ({
  prototype: false,
  detail: null,
  readFailed: false,
  post: vi.fn(),
  put: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/utils/prototype", async (original) => ({
  ...(await original()),
  isPrototypeMode: () => state.prototype,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: state.post, put: state.put },
  endpoints: {
    develop: {
      r4: { rerun: (id) => `/model-hub/r4/runs/${id}/rerun/` },
      experiment: {
        getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
      },
    },
  },
}));
vi.mock("src/api/contracts/strictBody", () => ({
  acceptedFields: () => ["append_only", "prompt_config"],
}));
vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => ({
    data: state.detail,
    isError: state.readFailed,
    refetch: () => {},
  }),
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptVersions: () => ({
    data: {
      pages: [
        {
          results: [
            { id: "version-a", template_version: "v1", is_draft: false },
            { id: "version-b", template_version: "v2", is_draft: false },
            { id: "draft-version", template_version: "Draft", is_draft: true },
          ],
        },
      ],
    },
  }),
}));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: ({ value, onChange, disabledClick }) => (
    <input
      aria-label="Model"
      value={value}
      disabled={disabledClick}
      onChange={(event) =>
        onChange({ target: { value: { model_name: event.target.value } } })
      }
    />
  ),
}));
vi.mock("src/sections/common/ux/toast", () => ({
  default: state.notify,
  notify: state.notify,
}));
vi.mock("../HarnessRecipe", () => ({ default: () => null }));
vi.mock("../SourceLogo", () => ({ default: () => null }));

const source = {
  id: "config-a",
  name: "Original",
  prompt_id: "prompt-a",
  prompt_name: "Saved prompt",
  prompt_version: "version-a",
  prompt_version_name: "v1",
  model: "gpt-4o-mini",
  model_config: {},
  model_params: { temperature: 0.2, max_tokens: 1000, top_p: 0.8 },
  configuration: { tool_choice: "auto" },
  output_format: "string",
};
const run = {
  run_id: "run-a",
  experiment_id: "experiment-a",
  dataset_id: "dataset-a",
  prompt_config_id: source.id,
  label: "#1",
  name: "Original",
  kind: "prompt",
  examples: 2,
  trials: 1,
  config: {
    kind: "prompt",
    prompt: { version: "v1" },
    model: {
      id: "gpt-4o-mini",
      params: { temperature: 0.2, max_tokens: 1000 },
    },
  },
};

// The server's answer when it saved the variant but could not confirm that
// execution started, as the app's request layer hands it over.
const UNCONFIRMED = {
  status: false,
  code: "experiment_admission_unconfirmed",
  detail: "Configuration saved, but execution admission is unconfirmed.",
  statusCode: 503,
};

const newClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

function open(props = {}) {
  const client = newClient();
  const close = vi.fn();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <RerunWithChangesDialog
        open
        onClose={close}
        run={run}
        failedRows={1}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { close, invalidate };
}

// The results page as the app shell runs it: closing the dialog unmounts it,
// and Rerun with changes… mounts a new one.
function Page() {
  const [shown, setShown] = React.useState(true);
  return (
    <>
      <button type="button" onClick={() => setShown(true)}>
        Open rerun
      </button>
      <button type="button" onClick={() => setShown(false)}>
        Leave page
      </button>
      {shown && (
        <RerunWithChangesDialog
          open
          onClose={() => setShown(false)}
          run={run}
        />
      )}
    </>
  );
}

function openPage() {
  const client = newClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const setData = vi.spyOn(client, "setQueryData");
  render(
    <QueryClientProvider client={client}>
      <Page />
    </QueryClientProvider>,
  );
  return { invalidate, setData };
}

// A same-tab logout: the results page goes away and every store is put back
// to its initial state, as the logout's store reset does first. The query
// client stays the same object for the next account.
function logOut() {
  fireEvent.click(screen.getByText("Leave page"));
  act(() => resetAllStores());
}

// Settles a held request, then lets every step of its answer run before the
// case checks what did not happen.
const settle = (finish) =>
  act(async () => {
    finish();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function submitChangedTemperature() {
  fireEvent.change(await screen.findByLabelText("Temperature"), {
    target: { value: "0.5" },
  });
  fireEvent.click(screen.getByTestId("rerun-run"));
  await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
}

const deferredPut = () => {
  const answer = {};
  state.put.mockReturnValueOnce(
    new Promise((resolve, reject) => {
      Object.assign(answer, { resolve, reject });
    }),
  );
  return answer;
};

beforeEach(() => {
  resetAllStores();
  state.prototype = false;
  state.readFailed = false;
  state.detail = {
    id: "experiment-a",
    dataset_id: "dataset-a",
    status: "Completed",
    experiment_type: "llm",
    snapshot_dataset_id: "snapshot-a",
    runs: [run],
    prompt_configs: [
      { ...source, id: "other-config", prompt_id: "other-prompt" },
      source,
    ],
  };
  state.post.mockReset().mockResolvedValue({
    data: { status: true, result: { run_id: "child", label: "#2" } },
  });
  state.put.mockReset().mockResolvedValue({
    data: { status: true, result: { status: "Queued", runs: null } },
  });
  state.notify.mockClear();
});
afterEach(cleanup);

describe("Rerun with changes on a real server", () => {
  it("appends the selected saved variant with changed model and numeric settings", async () => {
    const { close, invalidate } = open();
    fireEvent.change(await screen.findByLabelText("Model"), {
      target: { value: "gpt-4o" },
    });
    fireEvent.change(screen.getByLabelText("Temperature"), {
      target: { value: "0" },
    });
    fireEvent.change(screen.getByLabelText("Max tokens"), {
      target: { value: "256" },
    });
    fireEvent.click(screen.getByTestId("rerun-run"));
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(1));
    expect(state.put).toHaveBeenCalledWith(
      "/model-hub/experiments/v2/experiment-a/",
      {
        append_only: true,
        prompt_config: [
          {
            name: "Original variant",
            prompt_id: "prompt-a",
            prompt_version: "version-a",
            model: "gpt-4o",
            model_params: { temperature: 0, max_tokens: 256 },
            configuration: { tool_choice: "auto" },
            output_format: "string",
          },
        ],
      },
    );
    expect(state.post).not.toHaveBeenCalled();
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["experiment", "experiment-a"],
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["develop", "dataset-runs", "dataset-a"],
    });
    expect(state.notify).toHaveBeenCalledWith(
      "Started a new variant. Earlier variants and results are kept.",
      { variant: "success" },
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("appends a changed published prompt version without overwriting the original", async () => {
    open();
    fireEvent.mouseDown(await screen.findByLabelText("Published version"));
    expect(
      screen.queryByRole("option", { name: "Draft" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: "v2" }));
    fireEvent.click(screen.getByTestId("rerun-run"));
    await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
    const body = state.put.mock.calls[0][1];
    expect(body.append_only).toBe(true);
    expect(body.prompt_config).toHaveLength(1);
    expect(body.prompt_config[0]).not.toHaveProperty("id");
    expect(body.prompt_config[0]).toMatchObject({
      prompt_id: "prompt-a",
      prompt_version: "version-b",
      model: { name: "gpt-4o-mini", config: {}, display_name: "gpt-4o-mini" },
      model_params: { temperature: 0.2, max_tokens: 1000, top_p: 0.8 },
    });
    expect(state.post).not.toHaveBeenCalled();
  });

  it("clears copied numeric overrides without mutating the saved variant", async () => {
    const saved = {
      ...source,
      model_config: { temperature: 0.2, maxTokens: 1000 },
      configuration: {
        temperature: 0.2,
        max_tokens: 1000,
        tool_choice: "auto",
      },
    };
    const before = JSON.stringify(saved);
    state.detail.prompt_configs = [saved];
    open();
    fireEvent.change(await screen.findByLabelText("Temperature"), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText("Max tokens"), {
      target: { value: "" },
    });
    fireEvent.keyDown(screen.getByLabelText("Temperature"), {
      key: "Enter",
      ctrlKey: true,
    });
    await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
    expect(state.put.mock.calls[0][1].prompt_config[0]).toMatchObject({
      model: { name: "gpt-4o-mini", config: {} },
      model_params: { top_p: 0.8 },
      configuration: { tool_choice: "auto" },
    });
    const variant = state.put.mock.calls[0][1].prompt_config[0];
    for (const settings of [
      variant.model.config,
      variant.model_params,
      variant.configuration,
    ]) {
      expect(settings).not.toHaveProperty("temperature");
      expect(settings).not.toHaveProperty("max_tokens");
      expect(settings).not.toHaveProperty("maxTokens");
    }
    expect(JSON.stringify(saved)).toBe(before);
    expect(state.post).not.toHaveBeenCalled();
  });

  it.each(["all", "rows", "failed"])(
    "states the actual execution scope when opened from %s",
    async (initialScope) => {
      open({ initialScope, initialRowIds: ["row-a"] });
      const scope = await screen.findByText(
        "This re-run uses every row, one try each.",
      );
      expect(scope.tagName).toBe("P");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Rows to rerun")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Tries per row")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Use all snapshot rows" }),
      ).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("Temperature"), {
        target: { value: "0.5" },
      });
      expect(screen.getByTestId("rerun-run")).toBeEnabled();
      fireEvent.click(screen.getByTestId("rerun-run"));
      await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
      expect(Object.keys(state.put.mock.calls[0][1])).toEqual([
        "append_only",
        "prompt_config",
      ]);
      expect(state.post).not.toHaveBeenCalled();
      expect(source.model_params.temperature).toBe(0.2);
    },
  );

  it.each(["Queued", "Running"])(
    "cannot submit while the experiment is %s",
    async (status) => {
      state.detail.status = status;
      open();
      fireEvent.change(await screen.findByLabelText("Temperature"), {
        target: { value: "0.5" },
      });
      expect(screen.getByTestId("rerun-run")).toBeDisabled();
      fireEvent.keyDown(screen.getByLabelText("Temperature"), {
        key: "Enter",
        ctrlKey: true,
      });
      expect(state.put).not.toHaveBeenCalled();
      expect(state.post).not.toHaveBeenCalled();
    },
  );

  it("refuses a missing source identity rather than copying the first variant", async () => {
    open({ run: { ...run, prompt_config_id: "missing" } });
    expect(
      await screen.findByText(/saved prompt variant could not be found/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Model")).not.toBeInTheDocument();
    expect(state.put).not.toHaveBeenCalled();
  });

  it("retries the identical append after an unconfirmed outcome", async () => {
    state.put.mockRejectedValueOnce(UNCONFIRMED);
    open();
    fireEvent.change(await screen.findByLabelText("Temperature"), {
      target: { value: "0.5" },
    });
    fireEvent.click(screen.getByTestId("rerun-run"));
    await screen.findByRole("button", { name: "Retry submission" });
    expect(screen.getByLabelText("Temperature")).toBeDisabled();
    const first = state.put.mock.calls[0];
    fireEvent.click(screen.getByRole("button", { name: "Retry submission" }));
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(first);
    expect(state.post).not.toHaveBeenCalled();
  });
});

describe("Rerun with changes after an unconfirmed submission", () => {
  it("offers the identical retry again after the dialog is closed and reopened", async () => {
    state.put.mockRejectedValueOnce(UNCONFIRMED);
    openPage();
    await submitChangedTemperature();
    expect(
      await screen.findByRole("button", { name: "Retry submission" }),
    ).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The server did not confirm that this variant started. Retry this identical submission to confirm whether execution started.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument();
    // The saved variant leaves the experiment queued until it is confirmed.
    state.detail.status = "Queued";
    fireEvent.click(screen.getByText("Open rerun"));
    const retry = await screen.findByRole("button", {
      name: "Retry submission",
    });
    expect(retry).toBeEnabled();
    expect(screen.getByLabelText("Temperature")).toHaveValue(0.5);
    expect(screen.getByLabelText("Temperature")).toBeDisabled();
    expect(
      screen.queryByText(
        "Wait for the current run to finish before adding a variant.",
      ),
    ).not.toBeInTheDocument();
    fireEvent.click(retry);
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(state.put.mock.calls[0]);
    expect(state.put.mock.calls[1][1]).toBe(state.put.mock.calls[0][1]);
    await waitFor(() =>
      expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument(),
    );
    expect(state.notify).toHaveBeenCalledWith(
      "Started a new variant. Earlier variants and results are kept.",
      { variant: "success" },
    );
    expect(state.post).not.toHaveBeenCalled();
  });

  it("keeps the dialog and its answer through Escape and the backdrop while the request is in flight", async () => {
    const answer = deferredPut();
    const { close } = open();
    const dismiss = () => {
      fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
      const backdrop = document.querySelector(".MuiDialog-container");
      fireEvent.mouseDown(backdrop);
      fireEvent.click(backdrop);
    };
    fireEvent.change(await screen.findByLabelText("Temperature"), {
      target: { value: "0.5" },
    });
    // Both gestures close the dialog while nothing is in flight.
    dismiss();
    expect(close).toHaveBeenCalledTimes(2);
    close.mockClear();
    fireEvent.click(screen.getByTestId("rerun-run"));
    await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
    dismiss();
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    await act(async () => answer.reject(UNCONFIRMED));
    const retry = await screen.findByRole("button", {
      name: "Retry submission",
    });
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(retry);
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(state.put.mock.calls[0]);
  });

  it("keeps an unconfirmed answer that arrives after the dialog is gone", async () => {
    const answer = deferredPut();
    openPage();
    await submitChangedTemperature();
    fireEvent.click(screen.getByText("Leave page"));
    expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument();
    await act(async () => answer.reject(UNCONFIRMED));
    state.detail.status = "Queued";
    fireEvent.click(screen.getByText("Open rerun"));
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry submission" }),
    );
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(state.put.mock.calls[0]);
  });

  it("confirms a start that arrives after the dialog is gone", async () => {
    const answer = deferredPut();
    openPage();
    await submitChangedTemperature();
    fireEvent.click(screen.getByText("Leave page"));
    await act(async () =>
      answer.resolve({ data: { status: true, result: { status: "Queued" } } }),
    );
    await waitFor(() =>
      expect(state.notify).toHaveBeenCalledWith(
        "Started a new variant. Earlier variants and results are kept.",
        { variant: "success" },
      ),
    );
    fireEvent.click(screen.getByText("Open rerun"));
    expect(await screen.findByTestId("rerun-run")).toHaveTextContent(
      "Run new variant",
    );
    expect(
      screen.queryByRole("button", { name: "Retry submission" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the retry when a background read of the experiment fails", async () => {
    const answer = deferredPut();
    open();
    await submitChangedTemperature();
    state.readFailed = true;
    await act(async () => answer.reject(UNCONFIRMED));
    const retry = await screen.findByRole("button", {
      name: "Retry submission",
    });
    expect(
      screen.queryByText(/Could not read the experiment/),
    ).not.toBeInTheDocument();
    fireEvent.click(retry);
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(state.put.mock.calls[0]);
  });

  it("keeps the retry when the reopened dialog cannot read the experiment", async () => {
    state.put.mockRejectedValueOnce(UNCONFIRMED);
    openPage();
    await submitChangedTemperature();
    await screen.findByRole("button", { name: "Retry submission" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument();
    state.detail = undefined;
    state.readFailed = true;
    fireEvent.click(screen.getByText("Open rerun"));
    expect(
      await screen.findByText("Rerun #1 with changes"),
    ).toBeInTheDocument();
    fireEvent.click(
      await screen.findByRole("button", { name: "Retry submission" }),
    );
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(state.put.mock.calls[0]);
  });

  it("lets the draft change again after the server refuses it", async () => {
    state.put.mockRejectedValueOnce({
      status: false,
      message: "Select an accessible published version of the selected prompt.",
      statusCode: 400,
    });
    open();
    await submitChangedTemperature();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Select an accessible published version of the selected prompt.",
    );
    expect(
      screen.queryByRole("button", { name: "Retry submission" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Temperature")).toBeEnabled();
    expect(screen.getByTestId("rerun-run")).toHaveTextContent(
      "Run new variant",
    );
    fireEvent.change(screen.getByLabelText("Max tokens"), {
      target: { value: "256" },
    });
    fireEvent.click(screen.getByTestId("rerun-run"));
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(
      state.put.mock.calls[1][1].prompt_config[0].model_params,
    ).toMatchObject({
      temperature: 0.5,
      max_tokens: 256,
    });
  });
});

describe("Rerun with changes across a logout", () => {
  // One account submits, logs out while the request is in flight, and the
  // next account on this tab opens the same experiment.
  const expectFreshEditor = async () => {
    fireEvent.click(screen.getByText("Open rerun"));
    expect(await screen.findByTestId("rerun-run")).toHaveTextContent(
      "Run new variant",
    );
    expect(screen.getByLabelText("Temperature")).toHaveValue(0.2);
    expect(screen.getByLabelText("Temperature")).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Retry submission" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  };

  it("restores nothing when the unconfirmed answer arrives after logout", async () => {
    const answer = deferredPut();
    const { invalidate, setData } = openPage();
    await submitChangedTemperature();
    logOut();
    await settle(() => answer.reject(UNCONFIRMED));
    expect(state.notify).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(setData).not.toHaveBeenCalled();
    await expectFreshEditor();
    expect(state.put).toHaveBeenCalledOnce();
  });

  it("runs no callback when the start is confirmed after logout", async () => {
    const answer = deferredPut();
    const { invalidate, setData } = openPage();
    await submitChangedTemperature();
    logOut();
    await settle(() =>
      answer.resolve({ data: { status: true, result: { status: "Queued" } } }),
    );
    expect(state.notify).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(setData).not.toHaveBeenCalled();
    await expectFreshEditor();
    expect(state.put).toHaveBeenCalledOnce();
  });
});

describe("Rerun with changes in the prototype", () => {
  it("keeps the mock request including row scope and tries", async () => {
    state.prototype = true;
    open({
      initialScope: "rows",
      initialRowIds: ["row-a"],
      rowLabels: ["row-a"],
    });
    expect(
      screen.queryByText("This re-run uses every row, one try each."),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Rows to rerun")).toHaveValue("row-a");
    fireEvent.change(screen.getByLabelText("Temperature"), {
      target: { value: "0" },
    });
    fireEvent.change(screen.getByLabelText("Tries per row"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByTestId("rerun-run"));
    await waitFor(() => expect(state.post).toHaveBeenCalledOnce());
    expect(state.post).toHaveBeenCalledWith("/model-hub/r4/runs/run-a/rerun/", {
      params: {},
      changes: [{ axis: "params", field: "temperature", value: 0 }],
      execution: { mode: "same" },
      scope: "rows",
      row_ids: ["row-a"],
      trials: 2,
    });
    expect(state.put).not.toHaveBeenCalled();
  });
});
