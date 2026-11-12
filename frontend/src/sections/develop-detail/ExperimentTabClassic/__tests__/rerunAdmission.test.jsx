import {
  ui,
  WARNING,
  GENERIC,
  GATEWAY,
  envelope,
  fallbacks,
} from "./rerunAdmission.fixture";
import React from "react";
import { AxiosError } from "axios";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import * as handlers from "src/utils/queryErrorHandler";
import * as auth from "src/auth/context/jwt/utils";
import { resetUser } from "src/utils/Mixpanel";
import {
  handleExperimentRerunError,
  RERUN_ADMISSION_UNCONFIRMED_MESSAGE,
} from "src/utils/experimentRerunError";
import ExperimentTabHeader from "../ExperimentTabHeader";
import ExperimentRow from "src/sections/experiment-detail-classic/ExperimentRow";

const originalAdapter = axios.defaults.adapter;
const transport = vi.fn();
let client;
let delegated;
let timers;
let observer;
let normalized;
let location;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  auth.setIsRefreshing(false);
  auth.setSession("synthetic-session");
  delegated = vi.spyOn(handlers, "handleError");
  timers = vi.spyOn(globalThis, "setTimeout");
  location = {
    href: "http://localhost/dashboard/develop",
    pathname: "/dashboard/develop",
  };
  const browser = window;
  vi.stubGlobal(
    "window",
    new Proxy(browser, {
      get: (target, key) =>
        key === "location" ? location : Reflect.get(target, key, target),
    }),
  );
  transport.mockReset();
  transport.mockImplementation(() => {
    throw new Error("Unexpected adapter request");
  });
  axios.defaults.adapter = transport;
  normalized = [];
  observer = axios.interceptors.response.use(undefined, (error) => {
    normalized.push(error);
    // Freeze the normalized value before the real cache/local presentation.
    Object.freeze(error);
    return Promise.reject(error);
  });
  client = new QueryClient({
    mutationCache: new MutationCache({ onError: handlers.handleError }),
    // App retry:1 is queries-only. Do not force mutation retry:false in tests.
    defaultOptions: { queries: { retry: 1 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  timers.mock.calls.forEach(([, delay], index) => {
    if (delay === 500) clearTimeout(timers.mock.results[index].value);
  });
  axios.interceptors.response.eject(observer);
  axios.defaults.adapter = originalAdapter;
  auth.setSession(null);
  auth.clearTokens();
  auth.setIsRefreshing(false);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function reject(status, data) {
  transport.mockImplementation(async (config) => {
    throw new AxiosError(
      "Synthetic HTTP rejection",
      "ERR_BAD_RESPONSE",
      config,
      undefined,
      {
        status,
        data,
        config,
        headers: {},
        statusText: String(status),
      },
    );
  });
}
function start(caller) {
  render(
    <QueryClientProvider client={client}>
      {caller === "header" ? (
        <ExperimentTabHeader
          tabRef={{
            current: {
              refreshExperimentTab: ui.refresh,
              clearRowSelection: ui.clearSelection,
            },
          }}
          experimentSearch=""
          setExperimentSearch={vi.fn()}
          rowSelected={[
            { id: ui.id, name: "A" },
            { id: ui.secondId, name: "B" },
          ]}
          setRowSelected={vi.fn()}
        />
      ) : (
        <ExperimentRow />
      )}
    </QueryClientProvider>,
  );
  if (caller === "header") {
    fireEvent.click(screen.getByRole("button", { name: "Re-Run" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Re-Run",
      }),
    );
  } else
    fireEvent.click(screen.getByRole("button", { name: "Rerun Experiment" }));
}
async function settled(status) {
  await waitFor(() =>
    expect(client.getMutationCache().getAll()[0]?.state.status).toBe(status),
  );
}
function expectOneRequest(caller) {
  expect(transport).toHaveBeenCalledTimes(1);
  const [config] = transport.mock.calls[0];
  expect(config.url).toBe(endpoints.develop.experiment.rerun);
  expect(config.method).toBe("post");
  expect(JSON.parse(config.data)).toEqual({
    experiment_ids: caller === "header" ? [ui.id, ui.secondId] : [ui.id],
  });
  expect(client.getMutationCache().getAll()[0].options.retry).toBeUndefined();
}
function expectNoSuccess(caller) {
  expect(
    ui.snackbar.mock.calls.some(([, options]) => options.variant === "success"),
  ).toBe(false);
  if (caller === "header")
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  else expect(ui.refresh).not.toHaveBeenCalled();
  expect(ui.clearSelection).not.toHaveBeenCalled();
}
function expectDelegatedExactlyOnce() {
  // First call is MutationCache and is suppressed by the real mutation meta;
  // second is the local presenter's complete delegation, with no skip marker.
  expect(delegated).toHaveBeenCalledTimes(2);
  expect(delegated.mock.calls[0][3].options.meta).toEqual({
    errorHandled: true,
  });
  expect(delegated.mock.calls[1]).toEqual([normalized[0]]);
}

// Both paths run through real mutations; only visual leaves are substituted.
describe.each(["header", "classic row via useReRunExperiment"])(
  "%s rerun",
  (caller) => {
    it("shows one exact error from the real normalized envelope, without retry or success", async () => {
      reject(500, envelope());
      start(caller);
      await settled("error");
      expect(normalized[0]).toEqual({
        ...envelope(),
        statusCode: 500,
        transportCode: "ERR_BAD_RESPONSE",
        responseHeaders: { "x-request-id": undefined },
      });
      expect(normalized[0]).not.toHaveProperty("response");
      expect(ui.snackbar.mock.calls).toEqual([[WARNING, { variant: "error" }]]);
      expect(delegated).toHaveBeenCalledTimes(1);
      expect(delegated.mock.calls[0][3].options.meta).toEqual({
        errorHandled: true,
      });
      expectOneRequest(caller);
      expectNoSuccess(caller);
      if (caller === "header") {
        await waitFor(() => expect(ui.refresh).toHaveBeenCalledTimes(1));
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(transport).toHaveBeenCalledTimes(1);
      }
    });

    it.each(fallbacks)(
      "delegates %s without rendering arbitrary server text",
      async (_name, status, data) => {
        reject(status, data);
        start(caller);
        await settled("error");
        expectDelegatedExactlyOnce();
        // Missing result preserves handleError's existing silence, not a new toast.
        expect(ui.snackbar.mock.calls).toEqual(
          data.result ? [[GENERIC, { variant: "error" }]] : [],
        );
        expectOneRequest(caller);
        expectNoSuccess(caller);
      },
    );

    it.each([
      [
        400,
        "invalid",
        "Choose at least one experiment.",
        "Choose at least one experiment.",
      ],
      [400, "invalid", "Select at least one experiment.", GENERIC],
      [
        403,
        "tx1b_not_qualified",
        "Experiment target or execution context not found or unavailable.",
        GENERIC,
      ],
      [
        403,
        "permission_denied",
        "Ask the dataset owner for access.",
        "Ask the dataset owner for access.",
      ],
    ])(
      "retains the existing HTTP %s / %s fallback for %s",
      async (status, code, message, expected) => {
        reject(status, envelope(message, code));
        start(caller);
        await settled("error");
        expectDelegatedExactlyOnce();
        expect(ui.snackbar.mock.calls).toEqual([
          [expected, { variant: "error" }],
        ]);
        expectOneRequest(caller);
        expectNoSuccess(caller);
      },
    );

    it("leaves rate-limit handling to the existing handler", async () => {
      reject(429, envelope("Rate limit reached.", "rate_limited"));
      start(caller);
      await settled("error");
      expectDelegatedExactlyOnce();
      expect(ui.snackbar).not.toHaveBeenCalled();
      expectOneRequest(caller);
      expectNoSuccess(caller);
    });

    it("retains the gateway remedy without refreshing or clearing the session", async () => {
      reject(
        401,
        envelope("private upstream credentials", "gateway_request_rejected"),
      );
      const refresh = vi.spyOn(auth, "refreshTokenRequest");
      const clear = vi.spyOn(auth, "clearTokens");
      start(caller);
      await settled("error");
      expectDelegatedExactlyOnce();
      expect(normalized[0]).toBeInstanceOf(AxiosError);
      expect(ui.snackbar.mock.calls).toEqual([[GATEWAY, { variant: "error" }]]);
      expect(refresh).not.toHaveBeenCalled();
      expect(clear).not.toHaveBeenCalled();
      expect(localStorage.getItem("accessToken")).toBe("synthetic-session");
      expectOneRequest(caller);
      expectNoSuccess(caller);
    });

    it("does not bypass the existing expired-session logout", async () => {
      reject(401, envelope("Authentication required.", "not_authenticated"));
      const clear = vi.spyOn(auth, "clearTokens");
      start(caller);
      await settled("error");
      expectDelegatedExactlyOnce();
      expect(clear).toHaveBeenCalled();
      expect(resetUser).toHaveBeenCalled();
      expect(location.href).toBe("/auth/jwt/login");
      expect(localStorage.getItem("accessToken")).toBeNull();
      expect(ui.snackbar).not.toHaveBeenCalled();
      expectOneRequest(caller);
      expectNoSuccess(caller);
    });

    it("keeps true-success callbacks, toast, refresh and dialog behavior", async () => {
      transport.mockImplementation(async (config) => ({
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        data: {
          status: true,
          result: "Re-Run has started for the experiments.",
        },
      }));
      const invalidate = vi.spyOn(client, "invalidateQueries");
      start(caller);
      await settled("success");
      expect(delegated).not.toHaveBeenCalled();
      expect(ui.snackbar.mock.calls).toEqual([
        [
          caller === "header"
            ? "Re-Run has started for the selected experiments"
            : "Experiment re-run successfully initiated",
          { variant: "success" },
        ],
      ]);
      await waitFor(() => expect(ui.refresh).toHaveBeenCalledTimes(1));
      if (caller === "header") {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(invalidate).not.toHaveBeenCalled();
      } else {
        expect(ui.refresh).toHaveBeenCalledWith({ purge: true });
        expect(invalidate).toHaveBeenCalledTimes(1);
        expect(invalidate).toHaveBeenCalledWith(["experiment"]);
      }
      expectOneRequest(caller);
    });
  },
);

it("pins the approved programmatic constant and rejects non-normalized/coerced shapes", () => {
  expect(RERUN_ADMISSION_UNCONFIRMED_MESSAGE).toBe(WARNING);
  for (const error of [
    { ...envelope(), statusCode: "500" },
    { ...envelope(), status: 500 },
    { response: { status: 500, data: envelope() } },
    undefined,
  ]) {
    ui.snackbar.mockClear();
    delegated.mockClear();
    handleExperimentRerunError(error);
    expect(delegated).toHaveBeenCalledTimes(1);
    expect(delegated).toHaveBeenCalledWith(error);
    expect(
      ui.snackbar.mock.calls.some(([message]) => message === WARNING),
    ).toBe(false);
  }
});

it("does not grant unrelated mutations the rerun-only display exception", async () => {
  reject(500, envelope());
  const mutation = client.getMutationCache().build(client, {
    mutationFn: () =>
      axios.post(endpoints.develop.experiment.rerun, {
        experiment_ids: [ui.id],
      }),
  });
  await expect(mutation.execute()).rejects.toMatchObject({ statusCode: 500 });
  expect(delegated).toHaveBeenCalledTimes(1);
  expect(ui.snackbar.mock.calls).toEqual([[GENERIC, { variant: "error" }]]);
  expect(transport).toHaveBeenCalledTimes(1);
});
