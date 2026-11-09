// L2: the requests the DSX UI sends to dev's strict experiment and dataset
// routes, recorded at the axios adapter (the real client, the real request
// functions), in a real build (prototype mode off) and in prototype mode.
// Every recorded body is checked against the contract (strictBody).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import React from "react";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const proto = vi.hoisted(() => ({ on: false }));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => proto.on,
}));
vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn() },
}));
vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/auth/context/jwt/utils", () => ({
  addToQueue: vi.fn(),
  clearTokens: vi.fn(),
  getIsRefreshing: vi.fn(),
  getRefreshToken: vi.fn(),
  getRememberMe: vi.fn(),
  processQueue: vi.fn(),
  refreshTokenRequest: vi.fn(),
  setIsRefreshing: vi.fn(),
  setSession: vi.fn(),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
  closeSnackbar: vi.fn(),
}));

import client from "src/utils/axios";
import { strictViolations } from "src/api/contracts/strictBody";
import {
  perRunStopSupported,
  rerunCells,
  rerunExperiments,
  stopRuns,
} from "../experiment-run-actions";
import {
  RENAME_NOT_AVAILABLE,
  deleteExperiments,
  renameSupported,
  useDeleteExperiments,
  useRenameExperiment,
} from "../experiment-list";
import { uploadFormData } from "src/sections/develop/AddDatasetDrawer/uploadBody";
import {
  EDIT_RUN_PROMPT_BODY,
  withRerunScope,
} from "src/sections/develop-detail/AddColumn/rerunScope";

const EXP = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a";
const RUN = "0b5b7b9e-2222-4c8f-9a8e-0e2f8d1d2c3a";
const COL = "0b5b7b9e-3333-4c8f-9a8e-0e2f8d1d2c3a";
const DS = "0b5b7b9e-4444-4c8f-9a8e-0e2f8d1d2c3a";

const sent = [];
const bodyOf = (config) => {
  const d = config.data;
  if (typeof FormData !== "undefined" && d instanceof FormData)
    return Object.fromEntries([...d.keys()].map((k) => [k, d.get(k)]));
  try {
    return typeof d === "string" ? JSON.parse(d) : d ?? null;
  } catch {
    return d;
  }
};
const originalAdapter = client.defaults.adapter;
beforeEach(() => {
  sent.length = 0;
  proto.on = false;
  client.defaults.adapter = async (config) => {
    sent.push({
      method: String(config.method).toLowerCase(),
      url: new URL(config.url, "http://x.invalid").pathname,
      body: bodyOf(config),
      raw: config.data,
    });
    return { status: 200, data: { status: true, result: {} }, headers: {} };
  };
});
afterEach(() => {
  client.defaults.adapter = originalAdapter;
});

const wrapper = ({ children }) =>
  React.createElement(
    QueryClientProvider,
    { client: new QueryClient() },
    children,
  );

const clean = (r) => strictViolations(r.method, r.url, r.raw ?? r.body);

describe("stop (scoped run controls)", () => {
  it("a real build sends only the selected run IDs", async () => {
    expect(perRunStopSupported()).toBe(true);
    await stopRuns(EXP, [RUN]);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      method: "post",
      url: `/model-hub/experiments/v2/${EXP}/stop/`,
      body: { run_ids: [RUN] },
    });
    expect(clean(sent[0])).toEqual([]);
  });

  it("a real build can explicitly stop the whole experiment", async () => {
    await stopRuns(EXP);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      method: "post",
      url: `/model-hub/experiments/v2/${EXP}/stop/`,
      body: {},
    });
    expect(clean(sent[0])).toEqual([]);
  });

  it("prototype mode keeps the per-run stop its mocks answer", async () => {
    proto.on = true;
    await stopRuns(EXP, [RUN]);
    expect(sent[0].body).toEqual({ run_ids: [RUN] });
  });
});

describe("delete (dev's route answers DELETE only)", () => {
  it("deleteExperiments sends DELETE with the ids in the body", async () => {
    await deleteExperiments([EXP]);
    expect(sent[0]).toMatchObject({
      method: "delete",
      url: "/model-hub/experiments/v2/delete/",
      body: { experiment_ids: [EXP] },
    });
  });

  it("the list's delete mutation sends DELETE too", async () => {
    const { result } = renderHook(() => useDeleteExperiments(DS), {
      wrapper,
    });
    await act(() => result.current.mutateAsync({ ids: [EXP], runCount: 1 }));
    expect(sent.map((r) => r.method)).toEqual(["delete"]);
  });
});

describe("rename (ExperimentUpdateV2 declares no name)", () => {
  it("a real build sends nothing and says why", async () => {
    expect(renameSupported()).toBe(false);
    const { result } = renderHook(() => useRenameExperiment(DS), { wrapper });
    await act(async () => {
      await expect(
        result.current.mutateAsync({ id: EXP, name: "n", description: "" }),
      ).rejects.toThrow(RENAME_NOT_AVAILABLE);
    });
    expect(sent).toEqual([]);
  });
});

describe("re-run bodies the run actions send", () => {
  it("re-run and rerun-cells bodies are ones dev declares", async () => {
    await rerunExperiments([EXP]);
    await rerunCells(EXP, { source_ids: [RUN] });
    await rerunCells(EXP, { source_ids: [RUN], failed_only: true });
    await rerunCells(EXP, { user_eval_metric_ids: [RUN] });
    expect(sent).toHaveLength(4);
    sent.forEach((r) => expect(clean(r), r.url).toEqual([]));
  });
});

describe("upload a file (CreateDatasetFromLocalFileRequest)", () => {
  const file = new File(["q,a\n1,2\n"], "rows.csv", { type: "text/csv" });
  it("a real build posts only file, new_dataset_name and model_type", async () => {
    await client.post(
      "/model-hub/develops/create-dataset-from-local-file/",
      uploadFormData({
        name: "rows",
        modelType: "GenerativeLLM",
        file,
        roles: { q: "input", a: "expected" },
      }),
    );
    expect(Object.keys(sent[0].body).sort()).toEqual([
      "file",
      "model_type",
      "new_dataset_name",
    ]);
    expect(clean(sent[0])).toEqual([]);
  });

  it("prototype mode still sends the roles and import options", () => {
    const fd = uploadFormData(
      { name: "rows", modelType: "GenerativeLLM", file, roles: {} },
      true,
    );
    expect([...fd.keys()].sort()).toEqual([
      "column_roles",
      "file",
      "import_options",
      "model_type",
      "new_dataset_name",
    ]);
  });
});

describe("generated-column edits carry no scope", () => {
  it("rerun-operation and edit_run_prompt_column bodies are clean", async () => {
    await client.post(
      `/model-hub/columns/${COL}/rerun-operation/`,
      withRerunScope(
        { config: { json_key: "a" }, operation_type: "extract_json" },
        "failed",
      ),
    );
    await client.post(
      "/model-hub/develops/edit_run_prompt_column/",
      withRerunScope(
        { column_id: COL, dataset_id: DS, name: "out", config: {} },
        "changed",
        EDIT_RUN_PROMPT_BODY,
      ),
    );
    expect(sent.map((r) => "scope" in (r.body ?? {}))).toEqual([false, false]);
    sent.forEach((r) => expect(clean(r), r.url).toEqual([]));
  });
});
