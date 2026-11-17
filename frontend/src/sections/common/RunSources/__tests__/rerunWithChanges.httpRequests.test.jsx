import React from "react";
import PropTypes from "prop-types";
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
import { HTTP_RESEND_COPY } from "src/sections/common/Runs/httpResendCopy";
import {
  definitionSchema,
  droppedNestedFields,
  unknownFields,
} from "src/api/contracts/strictBody";
import RerunWithChangesOpen from "../RerunWithChangesOpen";

// Real production wrapper, dialog, editor, append body and request contract.
// Detail/Matrix reads and transport are controlled; no editor/submission mock.
const state = vi.hoisted(() => ({
  detail: null,
  readFailed: false,
  matrix: null,
  matrixFailed: false,
  put: vi.fn(),
  get: vi.fn(),
  notify: vi.fn(),
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("src/api/develop/r2", () => ({
  useMatrix: () => ({ data: state.matrix, isError: state.matrixFailed }),
}));
vi.mock("src/utils/axios", () => ({
  default: { put: state.put, get: state.get },
  endpoints: {
    develop: {
      experiment: {
        getExperimentDetails: (id) => `/model-hub/experiments/v2/${id}/`,
        experimentDetail: (id) => `/model-hub/experiments/v2/${id}/rows/`,
      },
    },
  },
}));
vi.mock("src/api/develop/runs", () => ({
  useExperimentDetail: () => ({
    data: state.detail,
    isError: state.readFailed,
    refetch: vi.fn(),
  }),
}));
vi.mock("src/api/develop/prompt", () => ({
  usePromptExecutions: () => ({ data: { pages: [] } }),
  usePromptVersions: () => ({ data: { pages: [] } }),
}));
vi.mock("src/components/custom-model-dropdown/CustomModelDropdown", () => ({
  default: () => null,
}));
vi.mock("src/sections/common/ux/toast", () => ({
  default: state.notify,
  notify: state.notify,
}));

const source = {
  id: "http-selected",
  name: "Selected endpoint",
  target_type: "http",
  http: {
    url: "https://selected.example/reply",
    method: "POST",
    headers: [{ name: "Authorization", secret_ref: "SAVED_ENDPOINT_SECRET" }],
    body_template: '{"q":"{{question}}"}',
    output_path: "$.reply",
    timeout_seconds: 15,
  },
  variable_mapping: { question: "column-a" },
};
const otherSource = {
  ...source,
  id: "http-first",
  name: "Other endpoint",
  http: { ...source.http, url: "https://other.example/reply" },
};
const run = {
  run_id: "run-selected",
  experiment_id: "experiment-a",
  dataset_id: "dataset-a",
  label: "#2",
  source: "http",
  task_kind: "external",
  native_config_id: source.id,
  // Deliberately misleading: HTTP must never use this prompt binding.
  prompt_config_id: otherSource.id,
};
const UNCONFIRMED = { statusCode: 503, detail: "Admission unconfirmed" };
const REFUSED = {
  statusCode: 403,
  detail: "Workspace write permission is required.",
};
const success = () => ({
  data: { status: true, result: { status: "Queued", runs: null } },
});

function Page({ initialRun, request, close }) {
  const [shown, setShown] = React.useState(true);
  const [currentRun, setRun] = React.useState(initialRun);
  return (
    <>
      <button onClick={() => setShown(true)}>Open rerun</button>
      <button onClick={() => setShown(false)}>Leave page</button>
      <button
        onClick={() => {
          setRun({
            ...run,
            run_id: "run-other",
            native_config_id: otherSource.id,
            label: "#1",
          });
          setShown(true);
        }}
      >
        Open other run
      </button>
      {shown && (
        <RerunWithChangesOpen
          request={request}
          run={currentRun}
          close={() => {
            close();
            setShown(false);
          }}
        />
      )}
    </>
  );
}

Page.propTypes = {
  initialRun: PropTypes.object,
  request: PropTypes.object,
  close: PropTypes.func.isRequired,
};

function open(initialRun = run, request = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const close = vi.fn();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const setData = vi.spyOn(client, "setQueryData");
  render(
    <QueryClientProvider client={client}>
      <Page initialRun={initialRun} request={request} close={close} />
    </QueryClientProvider>,
  );
  return { close, invalidate, setData };
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
const settle = (finish) =>
  act(async () => {
    finish();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
async function changeOutput(value = "$.answer") {
  fireEvent.change(await screen.findByLabelText("Output path"), {
    target: { value },
  });
}
async function submit() {
  await changeOutput();
  fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
  await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
}

beforeEach(() => {
  resetAllStores();
  state.readFailed = false;
  state.matrixFailed = false;
  state.matrix = {
    rows: [
      { row_id: "example-a", cells: { [run.run_id]: { trials: [0] } } },
      { row_id: "example-b", cells: { [run.run_id]: { trials: ["error"] } } },
      { row_id: "example-c", cells: { [run.run_id]: { trials: [1] } } },
    ],
  };
  state.detail = {
    id: "experiment-a",
    experiment_type: "llm",
    status: "Completed",
    http_configs: [otherSource, source],
    prompt_configs: [],
    runs: [run],
    earlier_outputs: [{ id: "old-output", value: "kept" }],
  };
  state.put.mockReset().mockResolvedValue(success());
  state.get.mockReset().mockResolvedValue({
    data: { result: { metadata: { total_rows: 8 } } },
  });
  state.notify.mockClear();
});
afterEach(cleanup);

describe("Persisted HTTP rerun requests", () => {
  it("edits the exact bound source and sends one keyed append with no copied id or acknowledgement", async () => {
    const before = JSON.stringify(state.detail);
    const answer = deferredPut();
    const { close, invalidate } = open();
    expect(await screen.findByLabelText("URL")).toHaveValue(source.http.url);
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "https://new.example/reply" },
    });
    fireEvent.mouseDown(screen.getByLabelText("Method"));
    fireEvent.click(screen.getByRole("option", { name: "PUT", exact: true }));
    fireEvent.change(screen.getByLabelText("Body template"), {
      target: { value: '{"input":"{{question}}"}' },
    });
    await changeOutput();
    fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
    expect(close).not.toHaveBeenCalled();
    expect(state.notify).not.toHaveBeenCalled();
    expect(state.put).toHaveBeenCalledWith(
      "/model-hub/experiments/v2/experiment-a/",
      {
        append_only: true,
        prompt_config: [
          {
            target_type: "http",
            name: "Selected endpoint variant",
            http: {
              ...source.http,
              url: "https://new.example/reply",
              method: "PUT",
              body_template: '{"input":"{{question}}"}',
              output_path: "$.answer",
            },
            variable_mapping: source.variable_mapping,
          },
        ],
      },
      { headers: { "Idempotency-Key": expect.stringMatching(/^fa-http-/) } },
    );
    const body = state.put.mock.calls[0][1];
    expect(body).not.toHaveProperty("acknowledge_http_side_effects");
    expect(body).not.toHaveProperty("requestBody");
    expect(body).not.toHaveProperty("key");
    expect(body.prompt_config[0]).not.toHaveProperty("id");
    expect(body.prompt_config[0]).not.toHaveProperty("prompt_id");
    expect(JSON.stringify(state.detail)).toBe(before);
    await settle(() => answer.resolve(success()));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    for (const queryKey of [
      ["experiment", "experiment-a"],
      ["develop", "dataset-runs", "dataset-a"],
      ["develop", "run-results", "dataset-a"],
      ["datasets", "list"],
    ])
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    expect(state.notify).toHaveBeenCalledWith(
      "Started a new variant. Earlier variants and results are kept.",
      { variant: "success" },
    );
  });

  it.each([
    ["missing experiment", { experiment_id: undefined }],
    ["missing run id", { run_id: undefined }],
    ["missing native id", { native_config_id: undefined }],
    ["foreign native id", { native_config_id: "absent" }],
    ["object source", { source: { kind: "http" } }],
    ["missing source", { source: undefined }],
    ["wrong task kind", { task_kind: "prompt" }],
    ["code", { source: "code" }],
    ["traces", { source: "traces" }],
    ["upload", { source: "upload" }],
  ])("refuses %s without choosing the first HTTP source", async (_, patch) => {
    open({ ...run, ...patch });
    expect(await screen.findByText(/no request was sent/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("URL")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Run new variant" }),
    ).not.toBeInTheDocument();
    expect(state.put).not.toHaveBeenCalled();
  });

  it.each([
    ["http", "external"],
    ["code", "code"],
    ["traces", "external"],
    ["upload", undefined],
  ])(
    "never falls back to a prompt config for an unbound %s run",
    async (sourceKind, taskKind) => {
      state.detail.http_configs = [];
      state.detail.prompt_configs = [
        {
          id: run.prompt_config_id,
          prompt_id: "prompt-a",
          prompt_version: "version-a",
          model: "saved-model",
        },
      ];
      open({
        ...run,
        source: sourceKind,
        task_kind: taskKind,
        native_config_id: undefined,
      });
      expect(
        await screen.findByText(/no request was sent/i),
      ).toBeInTheDocument();
      expect(screen.queryByLabelText("Temperature")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("URL")).not.toBeInTheDocument();
      expect(state.put).not.toHaveBeenCalled();
    },
  );

  it("requires a matching HTTP config, not merely a matching id", async () => {
    state.detail.http_configs = [
      otherSource,
      { ...source, target_type: "agent" },
    ];
    open();
    expect(await screen.findByText(/no request was sent/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("URL")).not.toBeInTheDocument();
    expect(state.put).not.toHaveBeenCalled();
  });

  it("shows all-row warning/count copy and validation; closing sends nothing", async () => {
    open();
    expect(
      await screen.findByText(HTTP_RESEND_COPY.appendSameEndpoint),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "The new variant sends 8 requests to its endpoint, one for each of the experiment's 8 rows.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(HTTP_RESEND_COPY.appendCopied)).toBeInTheDocument();
    const action = screen.getByRole("button", { name: "Run new variant" });
    expect(action).toBeDisabled();
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "/relative" },
    });
    expect(action).toBeDisabled();
    fireEvent.click(action);
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: source.http.url },
    });
    await changeOutput();
    expect(action).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument();
    expect(state.put).not.toHaveBeenCalled();
  });

  it("blocks a new variant while the experiment is running", async () => {
    state.detail.status = "Running";
    open();
    await changeOutput();
    expect(
      screen.getByRole("button", { name: "Run new variant" }),
    ).toBeDisabled();
    expect(state.put).not.toHaveBeenCalled();
  });

  it.each([new Error("Network Error"), UNCONFIRMED])(
    "keeps identical body/key and held draft through unmount, another run and failed detail read (%s)",
    async (error) => {
      state.put.mockRejectedValueOnce(error);
      open();
      await submit();
      await screen.findByRole("button", { name: "Check again" });
      const first = state.put.mock.calls[0];
      expect(
        screen.getByText(HTTP_RESEND_COPY.checkAgainHelp),
      ).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument();
      state.detail = undefined;
      state.readFailed = true;
      fireEvent.click(screen.getByText("Open other run"));
      expect(await screen.findByLabelText("URL")).toHaveValue(source.http.url);
      expect(screen.getByLabelText("Output path")).toHaveValue("$.answer");
      expect(screen.getByLabelText("Output path")).toBeDisabled();
      expect(screen.getByText("Rerun #2 with changes")).toBeInTheDocument();
      expect(state.put).toHaveBeenCalledOnce();
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
      expect(state.put.mock.calls[1]).toEqual(first);
      expect(state.put.mock.calls[1][1]).toBe(first[1]);
      await waitFor(() =>
        expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument(),
      );
    },
  );

  it("blocks duplicate inflight clicks and preserves an answer after leaving the page", async () => {
    const answer = deferredPut();
    const { close } = open();
    await changeOutput();
    const action = screen.getByRole("button", { name: "Run new variant" });
    act(() => {
      fireEvent.click(action);
      fireEvent.click(action);
    });
    await waitFor(() => expect(state.put).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Leave page"));
    fireEvent.click(screen.getByText("Open other run"));
    expect(await screen.findByLabelText("URL")).toHaveValue(source.http.url);
    expect(
      screen.getByRole("button", { name: "Run new variant" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByText("Leave page"));
    await settle(() => answer.reject(UNCONFIRMED));
    state.detail.status = "Queued";
    fireEvent.click(screen.getByText("Open rerun"));
    fireEvent.click(await screen.findByRole("button", { name: "Check again" }));
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(state.put.mock.calls[1]).toEqual(state.put.mock.calls[0]);
  });

  it("shows a definite refusal, then edits and explicitly sends with a fresh key and no stale error", async () => {
    state.put.mockRejectedValueOnce(REFUSED);
    open();
    await submit();
    expect(await screen.findByText(REFUSED.detail)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Check again" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Output path")).toBeEnabled();
    const first = state.put.mock.calls[0];
    const answer = deferredPut();
    await changeOutput("$.edited");
    expect(state.put).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
    await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(REFUSED.detail)).not.toBeInTheDocument();
    expect(state.put.mock.calls[1][2].headers["Idempotency-Key"]).not.toBe(
      first[2].headers["Idempotency-Key"],
    );
    expect(state.put.mock.calls[1][1].prompt_config[0].http.output_path).toBe(
      "$.edited",
    );
    await settle(() => answer.resolve(success()));
    await waitFor(() =>
      expect(screen.queryByTestId("rerun-dialog")).not.toBeInTheDocument(),
    );
  });

  it.each(["success", "refusal", "unknown"])(
    "ignores late %s after reset even while the new session owns a pending action",
    async (outcome) => {
      const old = deferredPut();
      const { close, invalidate, setData } = open();
      await submit();
      fireEvent.click(screen.getByText("Leave page"));
      act(() => resetAllStores());
      const current = deferredPut();
      fireEvent.click(screen.getByText("Open rerun"));
      expect(await screen.findByLabelText("Output path")).toHaveValue(
        "$.reply",
      );
      await changeOutput("$.new-session");
      fireEvent.click(screen.getByRole("button", { name: "Run new variant" }));
      await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
      const currentKey = state.put.mock.calls[1][2].headers["Idempotency-Key"];
      expect(currentKey).not.toBe(
        state.put.mock.calls[0][2].headers["Idempotency-Key"],
      );
      await settle(() => {
        if (outcome === "success") old.resolve(success());
        else old.reject(outcome === "refusal" ? REFUSED : UNCONFIRMED);
      });
      expect(close).not.toHaveBeenCalled();
      expect(state.notify).not.toHaveBeenCalled();
      expect(invalidate).not.toHaveBeenCalled();
      expect(setData).not.toHaveBeenCalled();
      expect(screen.getByLabelText("Output path")).toHaveValue("$.new-session");
      expect(screen.getByLabelText("Output path")).toBeDisabled();
      expect(screen.queryByText(REFUSED.detail)).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Check again" }),
      ).not.toBeInTheDocument();
      await settle(() => current.reject(UNCONFIRMED));
      fireEvent.click(
        await screen.findByRole("button", { name: "Check again" }),
      );
      await waitFor(() => expect(state.put).toHaveBeenCalledTimes(3));
      expect(state.put.mock.calls[2]).toEqual(state.put.mock.calls[1]);
      await waitFor(() => expect(close).toHaveBeenCalledOnce());
      expect(state.notify).toHaveBeenCalledOnce();
    },
  );
});

describe("Scoped HTTP variant requests", () => {
  it("declares the parent-bound selection in the saved request contract", () => {
    expect(
      definitionSchema("ExperimentUpdateV2").properties.http_selection,
    ).toEqual({
      $ref: "#/definitions/HttpAppendSelection",
    });
    expect(definitionSchema("HttpAppendSelection")).toEqual({
      required: ["parent_run_id", "scope"],
      type: "object",
      properties: {
        parent_run_id: {
          title: "Parent run id",
          type: "string",
          format: "uuid",
        },
        scope: {
          title: "Scope",
          type: "string",
          enum: ["rows", "failed"],
        },
        example_ids: {
          type: "array",
          items: { type: "string", format: "uuid" },
          maxItems: 10000,
        },
      },
    });
  });

  it.each([
    ["rows", ["example-b", "example-a", "example-b"], "selected"],
    ["failed", [], "failed"],
  ])(
    "keeps the parent and %s scope through the production wrapper and recovery",
    async (scope, rowIds, label) => {
      state.put.mockRejectedValueOnce(UNCONFIRMED);
      open(run, { scope, rowIds });
      const countLine =
        `The new variant sends 2 requests to its endpoint, one for each of ` +
        `this run's 2 ${label} rows.`;
      expect(await screen.findByText(countLine)).toBeInTheDocument();
      await submit();
      await screen.findByRole("button", { name: "Check again" });
      const first = state.put.mock.calls[0];
      expect(first[1]).toEqual({
        append_only: true,
        prompt_config: [
          {
            target_type: "http",
            name: "Selected endpoint variant",
            http: { ...source.http, output_path: "$.answer" },
            variable_mapping: source.variable_mapping,
          },
        ],
        http_selection: {
          parent_run_id: run.run_id,
          scope,
          ...(scope === "rows"
            ? { example_ids: ["example-b", "example-a"] }
            : {}),
        },
      });
      expect(unknownFields(first[1], "ExperimentUpdateV2")).toEqual([]);
      expect(droppedNestedFields(first[1], "ExperimentUpdateV2")).toEqual([]);
      expect(first[2]).toEqual({
        headers: { "Idempotency-Key": expect.stringMatching(/^fa-http-/) },
      });
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      state.matrix = undefined;
      state.matrixFailed = true;
      state.readFailed = true;
      fireEvent.click(screen.getByText("Open other run"));
      expect(await screen.findByText(countLine)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Check again" }));
      await waitFor(() => expect(state.put).toHaveBeenCalledTimes(2));
      expect(state.put.mock.calls[1]).toEqual(first);
      expect(state.put.mock.calls[1][1]).toBe(first[1]);
    },
  );

  it.each(["empty rows", "failed read", "no failures"])(
    "does not send all rows for %s",
    async (reason) => {
      if (reason === "failed read") {
        state.matrix = undefined;
        state.matrixFailed = true;
      }
      if (reason === "no failures") state.matrix = { rows: [] };
      open(run, { scope: reason === "empty rows" ? "rows" : "failed" });
      await changeOutput();
      const action = screen.getByRole("button", { name: "Run new variant" });
      expect(action).toBeDisabled();
      fireEvent.click(action);
      expect(state.put.mock.calls).toEqual([]);
    },
  );
});
