// Real header, real failed-row confirmation and real selection models.
// Only data hooks and the API transport stand in for the server.
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAllStores } from "src/utils/zustand";
import {
  HTTP_RESEND_COPY as COPY,
  newHttpActionKey,
} from "src/sections/common/Runs/httpResendCopy";
import ExperimentRow from "../ExperimentRow";

const EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e01";
const OTHER_EXP = "6d0a3c1e-5b7e-4d7a-9a51-1f6f3c2b8e02";
const HTTP = "0f3e2d1c-4b5a-4c6d-8e7f-9a0b1c2d3e4f";
const NATIVE = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const PASSED = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e";
const EXTERNAL = "3c4d5e6f-7a8b-4c9d-8e0f-2a3b4c5d6e7f";

const server = vi.hoisted(() => ({
  sent: [],
  replies: [],
  toasts: [],
  detail: null,
  matrix: null,
  listeners: new Set(),
  emit() {
    this.listeners.forEach((listener) => listener());
  },
  async post(...args) {
    const [url, body, config] = args;
    this.sent.push({
      url,
      body: { ...body, source_ids: body.source_ids?.slice() },
      config: config ? { ...config, headers: { ...config.headers } } : undefined,
      argc: args.length,
    });
    return this.replies.length
      ? this.replies.shift()()
      : { status: 202, data: { status: true, result: {} } };
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: {
      defaults: actual.default.defaults,
      interceptors: actual.default.interceptors,
      get: vi.fn(async () => ({ data: { status: true, result: {} } })),
      post: (...args) => server.post(...args),
    },
  };
});
vi.mock("src/api/develop/runs", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (listener) => {
    server.listeners.add(listener);
    return () => server.listeners.delete(listener);
  };
  return {
    useExperimentDetail: () => ({
      data: useSyncExternalStore(subscribe, () => server.detail),
    }),
    useDatasetRuns: () => ({ data: { results: [], pinned_run_id: null } }),
  };
});
vi.mock("src/api/develop/develop-detail", () => ({
  useDevelopDatasetList: () => ({
    data: [{ datasetId: "ds", name: "Synthetic rows" }],
  }),
}));
vi.mock("src/api/develop/r2", async (importOriginal) => ({
  ...(await importOriginal()),
  useMatrix: () => ({ data: server.matrix }),
  useRunConfigs: () => ({ data: { results: server.detail.runs } }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("src/sections/common/Access/useDatasetAccess", () => ({
  default: () => ({
    can: { view: true, download: true, edit: true, run: true, manage: false },
    reason: () => null,
    ask: [],
    isLoading: false,
    view: null,
  }),
}));
vi.mock("src/sections/common/Datasets/datasetKind", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetKind: () => ({ kind: "rows" }),
}));
vi.mock("notistack", () => ({
  enqueueSnackbar: (message, options) => server.toasts.push({ message, options }),
}));
vi.mock("src/sections/common/ux/toast", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (message, options) => {
    server.toasts.push({ message, options });
    return { undone: Promise.resolve(false) };
  },
}));
vi.mock("src/sections/common/Runs/httpResendCopy", async (importOriginal) => ({
  ...(await importOriginal()),
  newHttpActionKey: vi.fn(),
}));
vi.mock("../iterate/IterateButton", () => ({
  default: () => null,
  iterateDoorHref: () => "/x?iterate=1",
}));
vi.mock("src/sections/common/Runs/RunPickerPopover", () => ({
  default: () => null,
}));
vi.mock("../DevelopExperimentSelect", () => ({
  default: () => <span>Synthetic experiment</span>,
  experimentStatusOf: () => null,
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));

const runOf = (runId, letter, marker = { source: "native" }) => ({
  run_id: runId,
  experiment_id: EXP,
  experiment_ordinal: 1,
  letter,
  label: `#1${letter}`,
  name: `Variant ${letter}`,
  status: "Completed",
  progress: { total: 2, done: 2, errors: 0 },
  ...marker,
});
const httpRun = (marker = { source: "http" }) => runOf(HTTP, "A", marker);
const nativeRun = () => runOf(NATIVE, "B");
const passedRun = () => runOf(PASSED, "C", { source: "http" });
const externalRun = () => runOf(EXTERNAL, "D", { config: { kind: "code" } });

function fixture(runs = [httpRun(), nativeRun(), passedRun(), externalRun()], failed = [HTTP, NATIVE, EXTERNAL], experimentId = EXP) {
  server.detail = {
    id: experimentId,
    ordinal: 1,
    name: "Synthetic experiment",
    dataset_id: "ds",
    status: "Completed",
    runs: runs.map((run) => ({ ...run, experiment_id: experimentId })),
    runs_total: runs.length,
    results_availability: { reason: null },
  };
  server.matrix = {
    rows: [{
      row_id: "synthetic-row",
      cells: Object.fromEntries(runs.map((run) => [
        run.run_id,
        { trials: [failed.includes(run.run_id) ? (run.run_id === NATIVE ? "error" : false) : true] },
      ])),
    }],
  };
}
const poll = (...args) => act(() => {
  fixture(...args);
  server.emit();
});
const clients = [];
let navigate;
function Page() {
  navigate = useNavigate();
  return <ExperimentRow />;
}
const pageUrl = (id) => `/dashboard/develop/experiment/${id}/data?datasetId=ds`;
function openPage() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      // HTTP must override even an application-wide retry policy.
      mutations: { retry: 2, retryDelay: 0 },
    },
  });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[pageUrl(EXP)]}>
        <Routes>
          <Route path="/dashboard/develop/experiment/:experimentId/data" element={<Page />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
async function openConfirm() {
  fireEvent.click(screen.getByTestId("exp-rerun"));
  fireEvent.click(await screen.findByTestId("exp-rerun-failed"));
  return screen.findByRole("dialog");
}
function send(dialog) {
  fireEvent.click(within(dialog).getByRole("button", { name: /^(Rerun failed rows|Retry failed rows|Check again)$/ }));
}
async function close(dialog, label = /^(Keep results|Cancel|Close)$/) {
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}
const keyOf = (request) => request.config?.headers?.["Idempotency-Key"];
async function expectClosed() {
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
}
async function expectUnknown() {
  const button = await screen.findByRole("button", { name: COPY.checkAgain });
  expect(button.disabled).toBe(false);
  expect(screen.getByRole("dialog").textContent).toContain(COPY.checkAgainHelp);
  expect(server.sent).toHaveLength(1);
}
const rejectWith = (error) => async () => { throw error; };

beforeEach(() => {
  window.innerWidth = 1600;
  resetAllStores();
  server.sent.length = 0;
  server.replies.length = 0;
  server.toasts.length = 0;
  server.listeners.clear();
  let number = 0;
  newHttpActionKey.mockReset().mockImplementation(() => `synthetic-http-action-${++number}`);
  fixture();
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  resetAllStores();
});

describe("Experiment header failed-row HTTP acknowledgement", () => {
  it("CONTROL: menu, pre-confirm and cancel send nothing and create no key", async () => {
    openPage();
    expect(server.sent).toEqual([]);
    const dialog = await openConfirm();
    expect(server.sent).toEqual([]);
    expect(newHttpActionKey).not.toHaveBeenCalled();
    await close(dialog);
    expect(server.sent).toEqual([]);
    expect(newHttpActionKey).not.toHaveBeenCalled();
  });

  it.each([
    ["canonical source", { source: "http" }],
    ["config kind", { config: { kind: "http" } }],
    ["source kind", { source: { kind: "http" } }],
    ["kind", { kind: "http" }],
    ["target type", { target_type: "http" }],
  ])("warns and sends exact acknowledged failed-only selection with a header key for %s", async (_name, marker) => {
    fixture([httpRun(marker), nativeRun(), passedRun(), externalRun()]);
    openPage();
    const dialog = await openConfirm();
    expect(dialog.textContent).toContain(COPY.priorEffectMany);
    expect(dialog.textContent).toContain("Rows that passed are not sent again.");
    expect(dialog.textContent).toContain("#1A");
    expect(dialog.textContent).toContain("#1B");
    expect(dialog.textContent).not.toContain("#1C");
    expect(dialog.textContent).not.toContain("#1D");
    expect(server.sent).toEqual([]);
    send(dialog);
    await expectClosed();
    expect(server.sent).toEqual([{
      url: `/model-hub/experiments/v2/${EXP}/rerun-cells/`,
      body: { source_ids: [HTTP, NATIVE], failed_only: true, acknowledge_http_side_effects: true },
      config: { headers: { "Idempotency-Key": "synthetic-http-action-1" } },
      argc: 3,
    }]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });

  it("keeps the HTTP selection shown at confirmation when polling changes failures", async () => {
    openPage();
    const dialog = await openConfirm();
    poll([httpRun(), nativeRun(), passedRun()], [PASSED]);
    expect(dialog.textContent).toContain("#1A");
    expect(dialog.textContent).not.toContain("#1C");
    send(dialog);
    await expectClosed();
    expect(server.sent[0].body).toEqual({ source_ids: [HTTP, NATIVE], failed_only: true, acknowledge_http_side_effects: true });
  });

  it("blocks duplicate in-flight clicks and closing until the HTTP response settles", async () => {
    let resolve;
    server.replies.push(() => new Promise((done) => { resolve = done; }));
    openPage();
    const dialog = await openConfirm();
    const button = within(dialog).getByRole("button", { name: /^(Rerun failed rows|Retry failed rows)$/ });
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    await waitFor(() => expect(server.sent).toHaveLength(1));
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(button.disabled).toBe(true);
    const cancel = within(dialog).getByRole("button", { name: /^(Cancel|Keep results)$/ });
    expect(cancel.disabled).toBe(true);
    fireEvent.click(cancel);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ data: { status: true, result: {} } }));
    await expectClosed();
    expect(server.sent).toHaveLength(1);
  });

  it.each([
    ["network loss", new Error("Synthetic connection lost")],
    ["server failure", { response: { status: 503 } }],
  ])("does not auto-retry %s and checks again with exactly the same action", async (_name, error) => {
    server.replies.push(rejectWith(error));
    openPage();
    send(await openConfirm());
    await expectUnknown();
    send(screen.getByRole("dialog"));
    await expectClosed();
    expect(server.sent).toHaveLength(2);
    expect(server.sent[1]).toEqual(server.sent[0]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });

  it("close and reopen recovers the original unknown action rather than the new selection", async () => {
    server.replies.push(rejectWith({ statusCode: 502 }));
    openPage();
    send(await openConfirm());
    await expectUnknown();
    await close(screen.getByRole("dialog"), COPY.close);
    poll([httpRun(), nativeRun(), passedRun()], [PASSED]);
    const reopened = await openConfirm();
    expect(reopened.textContent).toContain(COPY.checkAgainHelp);
    expect(reopened.textContent).toContain("#1A");
    expect(reopened.textContent).not.toContain("#1C");
    expect(server.sent).toHaveLength(1);
    send(reopened);
    await expectClosed();
    expect(server.sent[1]).toEqual(server.sent[0]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });

  it("keeps explicit recovery reachable when polling reports no failed rows", async () => {
    server.replies.push(rejectWith({ statusCode: 500 }));
    openPage();
    send(await openConfirm());
    await expectUnknown();
    await close(screen.getByRole("dialog"), COPY.close);
    poll([httpRun(), nativeRun(), passedRun()], []);
    const reopened = await openConfirm();
    expect(reopened.textContent).toContain(COPY.checkAgainHelp);
    send(reopened);
    await expectClosed();
    expect(server.sent[1]).toEqual(server.sent[0]);
  });

  it("reports an explicit refusal without retry and requires fresh consent and key afterward", async () => {
    server.replies.push(rejectWith({ statusCode: 403, detail: "This synthetic action was refused." }));
    openPage();
    send(await openConfirm());
    await expectClosed();
    expect(server.sent).toHaveLength(1);
    expect(server.toasts).toContainEqual({
      message: "This synthetic action was refused.",
      options: { variant: "error" },
    });
    expect(keyOf(server.sent[0])).toBe("synthetic-http-action-1");
    const next = await openConfirm();
    expect(next.textContent).toContain(COPY.priorEffectMany);
    expect(within(next).queryByRole("button", { name: COPY.checkAgain })).toBeNull();
    expect(server.sent).toHaveLength(1);
    send(next);
    await expectClosed();
    expect(keyOf(server.sent[1])).toBe("synthetic-http-action-2");
  });

  it("creates a new key only for a newly confirmed action after success", async () => {
    openPage();
    send(await openConfirm());
    await expectClosed();
    expect(keyOf(server.sent[0])).toBe("synthetic-http-action-1");
    const next = await openConfirm();
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
    send(next);
    await expectClosed();
    expect(keyOf(server.sent[1])).toBe("synthetic-http-action-2");
  });

  it("isolates experiment identities and recovers the original action on returning", async () => {
    server.replies.push(rejectWith({ statusCode: 503 }));
    openPage();
    send(await openConfirm());
    await expectUnknown();
    await close(screen.getByRole("dialog"), COPY.close);
    act(() => {
      fixture([passedRun()], [PASSED], OTHER_EXP);
      server.emit();
      navigate(pageUrl(OTHER_EXP));
    });
    send(await openConfirm());
    await expectClosed();
    expect(server.sent[1].url).toBe(`/model-hub/experiments/v2/${OTHER_EXP}/rerun-cells/`);
    expect(server.sent[1].body.source_ids).toEqual([PASSED]);
    expect(keyOf(server.sent[1])).toBe("synthetic-http-action-2");
    act(() => {
      fixture();
      server.emit();
      navigate(pageUrl(EXP));
    });
    const original = await openConfirm();
    expect(original.textContent).toContain(COPY.checkAgainHelp);
    send(original);
    await expectClosed();
    expect(server.sent[2]).toEqual(server.sent[0]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["native-only experiment", [nativeRun()]],
    ["native selection with unselected HTTP runs", [nativeRun(), passedRun()]],
  ])("CONTROL: preserves the native request and confirmation for %s", async (_name, runs) => {
    fixture(runs, [NATIVE]);
    openPage();
    const dialog = await openConfirm();
    expect(dialog.textContent).not.toContain(COPY.priorEffectMany);
    expect(dialog.textContent).not.toContain(COPY.checkAgainHelp);
    expect(within(dialog).getByRole("button", { name: "Keep results" })).toBeTruthy();
    send(dialog);
    await expectClosed();
    expect(server.sent).toEqual([{
      url: `/model-hub/experiments/v2/${EXP}/rerun-cells/`,
      body: { source_ids: [NATIVE], failed_only: true },
      config: undefined,
      argc: 2,
    }]);
    expect(newHttpActionKey).not.toHaveBeenCalled();
  });

  it("recovers the original unknown action after a real header unmount and remount", async () => {
    server.replies.push(rejectWith({ statusCode: 503 }));
    const page = openPage();
    send(await openConfirm());
    await expectUnknown();
    const original = server.sent[0];
    page.unmount();
    fixture([httpRun(), nativeRun(), passedRun()], [PASSED]);
    openPage();
    expect(screen.queryByRole("dialog")).toBeNull();
    const recovered = await openConfirm();
    expect(recovered.textContent).toContain(COPY.checkAgainHelp);
    expect(recovered.textContent).toContain("#1A");
    expect(recovered.textContent).not.toContain("#1C");
    expect(server.sent).toHaveLength(1);
    send(recovered);
    await expectClosed();
    expect(server.sent).toEqual([original, original]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });

  it("keeps an in-flight action busy across real remount and recovers its later unknown outcome", async () => {
    let reject;
    server.replies.push(() => new Promise((_resolve, fail) => { reject = fail; }));
    const page = openPage();
    send(await openConfirm());
    await waitFor(() => expect(server.sent).toHaveLength(1));
    const original = server.sent[0];
    page.unmount();
    fixture([httpRun(), nativeRun(), passedRun()], [PASSED]);
    openPage();
    const recovered = await openConfirm();
    expect(recovered.textContent).toContain("#1A");
    expect(recovered.textContent).not.toContain("#1C");
    const button = within(recovered).getByRole("button", { name: COPY.failedRowsConfirm });
    expect(button.disabled).toBe(true);
    expect(within(recovered).getByRole("button", { name: COPY.cancel }).disabled).toBe(true);
    act(() => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    expect(server.sent).toHaveLength(1);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
    await act(async () => reject({ statusCode: 503 }));
    await expectUnknown();
    send(screen.getByRole("dialog"));
    await expectClosed();
    expect(server.sent).toEqual([original, original]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });

  it("settles a remounted in-flight dialog before admitting a fresh action", async () => {
    let resolve;
    server.replies.push(() => new Promise((done) => { resolve = done; }));
    const page = openPage();
    send(await openConfirm());
    await waitFor(() => expect(server.sent).toHaveLength(1));
    page.unmount();
    fixture([httpRun(), nativeRun(), passedRun()], [PASSED]);
    openPage();
    const recovered = await openConfirm();
    expect(within(recovered).getByRole("button", { name: COPY.failedRowsConfirm }).disabled).toBe(true);
    expect(server.sent).toHaveLength(1);
    await act(async () => resolve({ data: { status: true, result: {} } }));
    await expectClosed();
    const fresh = await openConfirm();
    expect(fresh.textContent).toContain("#1C");
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
    send(fresh);
    await expectClosed();
    expect(server.sent).toHaveLength(2);
    expect(server.sent[1].body).toEqual({ source_ids: [PASSED], failed_only: true, acknowledge_http_side_effects: true });
    expect(keyOf(server.sent[1])).toBe("synthetic-http-action-2");
  });

  it.each(["unknown", "success", "refusal"])("ignores a pre-reset in-flight %s settlement without replacing the new account action", async (outcome) => {
    let resolve;
    let reject;
    server.replies.push(() => new Promise((done, fail) => { resolve = done; reject = fail; }));
    const page = openPage();
    send(await openConfirm());
    await waitFor(() => expect(server.sent).toHaveLength(1));
    page.unmount();
    act(() => resetAllStores());
    fixture([httpRun(), nativeRun(), passedRun()], [PASSED]);
    server.replies.push(rejectWith({ statusCode: 503 }));
    const nextPage = openPage();
    const fresh = await openConfirm();
    expect(fresh.textContent).not.toContain(COPY.checkAgainHelp);
    send(fresh);
    await screen.findByRole("button", { name: COPY.checkAgain });
    expect(server.sent).toHaveLength(2);
    const current = server.sent[1];
    expect(current.body.source_ids).toEqual([PASSED]);
    expect(keyOf(current)).toBe("synthetic-http-action-2");
    const toastCount = server.toasts.length;
    await act(async () => {
      if (outcome === "success") resolve({ data: { status: true, result: {} } });
      else reject({ statusCode: outcome === "refusal" ? 403 : 503 });
    });
    expect(server.toasts).toHaveLength(toastCount);
    expect(screen.getByRole("button", { name: COPY.checkAgain }).disabled).toBe(false);
    expect(server.sent).toHaveLength(2);
    nextPage.unmount();
    fixture();
    openPage();
    const recovered = await openConfirm();
    expect(recovered.textContent).toContain(COPY.checkAgainHelp);
    expect(recovered.textContent).toContain("#1C");
    expect(recovered.textContent).not.toContain("#1A");
    send(recovered);
    await expectClosed();
    expect(server.sent).toHaveLength(3);
    expect(server.sent[2]).toEqual(current);
    expect(newHttpActionKey).toHaveBeenCalledTimes(2);
  });

  it("invalidates the mounted unknown confirmation on store reset", async () => {
    server.replies.push(rejectWith({ statusCode: 503 }));
    openPage();
    send(await openConfirm());
    await expectUnknown();
    act(() => resetAllStores());
    await expectClosed();
    const fresh = await openConfirm();
    expect(fresh.textContent).toContain(COPY.priorEffectMany);
    expect(fresh.textContent).not.toContain(COPY.checkAgainHelp);
    expect(server.sent).toHaveLength(1);
    send(fresh);
    await expectClosed();
    expect(server.sent).toHaveLength(2);
    expect(keyOf(server.sent[1])).toBe("synthetic-http-action-2");
  });

  it.each(["polling adds HTTP failures", "delayed config identifies HTTP"])("requires a second informed confirmation when %s in a native dialog", async (change) => {
    if (change === "polling adds HTTP failures") fixture([httpRun(), nativeRun()], [NATIVE]);
    else fixture([httpRun({ source: "native" }), nativeRun()], [HTTP, NATIVE]);
    openPage();
    const nativeDialog = await openConfirm();
    expect(nativeDialog.textContent).not.toContain(COPY.priorEffectMany);
    poll([httpRun({ config: { kind: "http" } }), nativeRun(), passedRun()], [HTTP, NATIVE]);
    send(nativeDialog);
    expect(server.sent).toEqual([]);
    expect(newHttpActionKey).not.toHaveBeenCalled();
    const informed = screen.getByRole("dialog");
    expect(informed.textContent).toContain(COPY.failedRowsTitle);
    expect(informed.textContent).toContain(COPY.priorEffectMany);
    expect(informed.textContent).toContain("#1A");
    expect(informed.textContent).toContain("#1B");
    poll([httpRun(), nativeRun(), passedRun()], [PASSED]);
    send(informed);
    await expectClosed();
    expect(server.sent).toEqual([{
      url: `/model-hub/experiments/v2/${EXP}/rerun-cells/`,
      body: { source_ids: [HTTP, NATIVE], failed_only: true, acknowledge_http_side_effects: true },
      config: { headers: { "Idempotency-Key": "synthetic-http-action-1" } },
      argc: 3,
    }]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });

  it.each(["initial send", "unknown recovery"])("honors a newly blocked rerun guard for HTTP %s after dialog open", async (phase) => {
    if (phase === "unknown recovery") server.replies.push(rejectWith({ statusCode: 503 }));
    openPage();
    const dialog = await openConfirm();
    if (phase === "unknown recovery") {
      send(dialog);
      await expectUnknown();
    }
    const prior = server.sent.slice();
    poll([httpRun(), {
      ...nativeRun(),
      status: "cancelled",
      completed_at: null,
      progress: { total: 2, done: 1, errors: 0 },
    }], [HTTP, NATIVE]);
    const button = within(dialog).getByRole("button", {
      name: phase === "unknown recovery" ? COPY.checkAgain : COPY.failedRowsConfirm,
    });
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(server.sent).toEqual(prior);
    expect(newHttpActionKey).toHaveBeenCalledTimes(prior.length);
    poll();
    expect(button.disabled).toBe(false);
    send(dialog);
    await expectClosed();
    expect(server.sent).toHaveLength(prior.length + 1);
    if (prior.length) expect(server.sent[1]).toEqual(prior[0]);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });
});
