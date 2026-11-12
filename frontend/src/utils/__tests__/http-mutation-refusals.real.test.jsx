// SRC-S1b-HTTP-FE-04: the typed HTTP mutation refusals are gone. Save, Append
// (a new HTTP variant or new rows), Retry cell and Retry failed rows of an
// HTTP experiment go through real Axios normalization, the request contract
// and the app's MutationCache handler to the server, once each, with no
// fallback or automatic re-send. Appends and retries carry their explicit
// Idempotency-Key; retries acknowledge the possible side effects. Only the
// transport adapter is replaced; no request leaves the test.
import React from "react";
import { AxiosError } from "axios";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
  useMutation,
} from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import client, { endpoints } from "src/utils/axios";
import { useUpdateExperiment } from "src/api/develop/experiment-create";
import { appendExperimentVariant } from "src/api/develop/experiment-append";
import { rerunCells } from "src/api/develop/experiment-run-actions";
import * as errorUtils from "../errorUtils";
import { handleError } from "../queryErrorHandler";

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("src/utils/Mixpanel", async () => ({
  // Route metadata reads Events at import time. Keep its real constants while
  // replacing analytics effects; do not mock the HTTP path under test.
  ...(await import("src/utils/Mixpanel/EventNames")),
  resetUser: vi.fn(),
  trackEvent: vi.fn(),
}));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
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
// The create-admission hook is not used by any of these mutations.
vi.mock("src/sections/develop-detail/Experiment/useCreateAdmission", () => ({
  useCreateAdmission: vi.fn(),
}));

const EXP = "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a";
const SOURCE = "0b5b7b9e-2222-4c8f-9a8e-0e2f8d1d2c3a";
const COLUMN = "0b5b7b9e-3333-4c8f-9a8e-0e2f8d1d2c3a";
const ROW = "0b5b7b9e-4444-4c8f-9a8e-0e2f8d1d2c3a";
const http = {
  url: "https://fixture.example/run",
  method: "POST",
  headers: [{ name: "Authorization", secret_ref: "TOKEN" }],
  body_template: '{"q":"{{question}}"}',
  output_path: "$.reply",
  timeout_seconds: 20,
};
// Edit keeps the source's identity; append sends a new variant without one.
const editBody = {
  prompt_config: [{
    target_type: "http", id: SOURCE, name: "HTTP source", http,
    variable_mapping: { question: COLUMN },
  }],
};
const appendBody = {
  append_only: true,
  prompt_config: [{
    target_type: "http", name: "HTTP source variant",
    http: { ...http, output_path: "$.answer" },
    variable_mapping: { question: COLUMN },
  }],
};
const appendRowsBody = { append_only: true, row_ids: [ROW] };
const cellBody = {
  cells: [{ column_id: COLUMN, row_id: ROW }],
  acknowledge_http_side_effects: true,
};
const failedBody = {
  source_ids: [SOURCE], failed_only: true, acknowledge_http_side_effects: true,
};
const KEY = "fa-http-0b5b7b9e-5555-4c8f-9a8e-0e2f8d1d2c3a";
const STALE_REFUSAL = {
  status: false,
  code: "http_update_unavailable",
  detail: "HTTP source editing and appending are not supported. Create a new experiment; use explicit whole-experiment rerun for unchanged settings.",
};
const originalAdapter = client.defaults.adapter;
const transport = vi.fn();
const success = vi.fn();
const failure = vi.fn();
const cacheError = vi.fn((...args) => handleError(...args));
let queryClient;

function MutationControls() {
  // As the drawer uses it: no retry option, so the app default applies.
  const update = useUpdateExperiment(EXP, {
    meta: { errorHandled: true }, onSuccess: success, onError: failure,
  });
  const append = useMutation({
    mutationFn: (body) => appendExperimentVariant(EXP, body, { idempotencyKey: KEY }),
    meta: { errorHandled: true }, onSuccess: success, onError: failure, retry: false,
  });
  const retry = useMutation({
    mutationFn: (body) => rerunCells(EXP, body, { idempotencyKey: KEY }),
    meta: { errorHandled: true }, onSuccess: success, onError: failure, retry: false,
  });
  return (
    <>
      <button type="button" onClick={() => update.mutate(editBody)}>Save</button>
      <button type="button" onClick={() => append.mutate(appendBody)}>Append</button>
      <button type="button" onClick={() => append.mutate(appendRowsBody)}>Append rows</button>
      <button type="button" onClick={() => retry.mutate(cellBody)}>Retry cell</button>
      <button type="button" onClick={() => retry.mutate(failedBody)}>Retry failed rows</button>
    </>
  );
}

function mount() {
  return render(
    <QueryClientProvider client={queryClient}>
      <SnackbarProvider autoHideDuration={null} preventDuplicate>
        <MutationControls />
      </SnackbarProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: cacheError }),
    defaultOptions: {
      mutations: { gcTime: Infinity },
      queries: { retry: false, gcTime: Infinity },
    },
  });
  transport.mockImplementation(async (config) => ({
    status: 200, statusText: "OK", headers: {}, config,
    data: { status: true, result: { id: EXP } },
  }));
  client.defaults.adapter = transport;
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  client.defaults.adapter = originalAdapter;
});

// [label, method, url, body, Idempotency-Key]. Save only saves: no key.
const ACTIONS = [
  ["Save", "put", () => endpoints.develop.experiment.update(EXP), editBody, undefined],
  ["Append", "put", () => endpoints.develop.experiment.getExperimentDetails(EXP), appendBody, KEY],
  ["Append rows", "put", () => endpoints.develop.experiment.getExperimentDetails(EXP), appendRowsBody, KEY],
  ["Retry cell", "post", () => endpoints.develop.experiment.reRunExperimentCell(EXP), cellBody, KEY],
  ["Retry failed rows", "post", () => endpoints.develop.experiment.reRunExperimentCell(EXP), failedBody, KEY],
];
const keyOf = (request) =>
  request.headers?.get?.("Idempotency-Key") ?? request.headers?.["Idempotency-Key"];

describe("HTTP mutations reach the server after the refusals were removed", () => {
  it("no longer exports the refusal presenter", () => {
    expect(errorUtils.getHttpMutationRefusalMessage).toBeUndefined();
  });

  it.each(ACTIONS)("%s sends its HTTP body once and succeeds", async (label, method, url, body, key) => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: label, exact: true }));
    await waitFor(() => expect(success).toHaveBeenCalledTimes(1));
    expect(failure).not.toHaveBeenCalled();
    expect(transport).toHaveBeenCalledTimes(1);
    const [request] = transport.mock.calls[0];
    expect(request.method).toBe(method);
    expect(request.url).toBe(url());
    expect(JSON.parse(request.data)).toEqual(body);
    expect(keyOf(request)).toBe(key);
  });

  it.each(ACTIONS)("%s is not sent again after an unknown outcome", async (label) => {
    transport.mockImplementation(async (config) => {
      throw new AxiosError("Network Error", AxiosError.ERR_NETWORK, config);
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: label, exact: true }));
    await waitFor(() => expect(failure).toHaveBeenCalledTimes(1));
    const [normalized] = failure.mock.calls[0];
    expect(normalized.statusCode).toBeUndefined();
    expect(normalized.transportCode).toBe(AxiosError.ERR_NETWORK);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(transport).toHaveBeenCalledTimes(1);
    expect(success).not.toHaveBeenCalled();
  });

  it("does not special-case a stale refusal body any more", async () => {
    transport.mockImplementation(async (config) => {
      throw new AxiosError(
        "Request failed with status code 400", AxiosError.ERR_BAD_REQUEST, config, undefined,
        { status: 400, statusText: "Bad Request", config, headers: {}, data: STALE_REFUSAL },
      );
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(failure).toHaveBeenCalledTimes(1));
    const [normalized] = failure.mock.calls[0];
    expect(normalized).toMatchObject({ ...STALE_REFUSAL, statusCode: 400 });
    expect(errorUtils.getRequestErrorMessage(normalized, "Could not save the experiment."))
      .toBe("Could not save the experiment.");
    handleError(normalized);
    expect(screen.queryByText(STALE_REFUSAL.detail)).toBeNull();
    expect(transport).toHaveBeenCalledTimes(1);
  });
});
