import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import { handleError } from "src/utils/queryErrorHandler";
import { useCreateWebhook, useUpdateWebhook } from "../hooks/useWebhooks";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  patch: vi.fn(),
  enqueueSnackbar: vi.fn(),
}));

// Only the axios instance is replaced; the endpoints are the real ones.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { post: mocks.post, patch: mocks.patch } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: mocks.enqueueSnackbar }));

const NAME_REFUSED =
  "Name can only contain letters, numbers, hyphens, and underscores";

// What axios.js rejects with when the server refuses the name (a 400 whose
// body is tfc/utils/api_errors.py build_error_envelope's), and when the
// server fails.
const NAME_REFUSAL = {
  status: false,
  detail: `name: ${NAME_REFUSED}`,
  message: `name: ${NAME_REFUSED}`,
  error: `name: ${NAME_REFUSED}`,
  result: `name: ${NAME_REFUSED}`,
  attr: "name",
  details: { name: [NAME_REFUSED] },
  statusCode: 400,
};
const SERVER_ERROR = { result: "Internal error", statusCode: 500 };

const WEBHOOK = {
  name: "ops_events",
  url: "https://hooks.example.com/ops",
  events: ["error.occurred"],
  is_active: true,
  headers: {},
};

const SAVES = [
  ["creating", useCreateWebhook, WEBHOOK, mocks.post],
  ["editing", useUpdateWebhook, { id: "wh-ops", ...WEBHOOK }, mocks.patch],
];

// Each failure with the app-wide toast's words for it: a refusal in the
// server's words, a server error as "Something went wrong".
const FAILURES = [
  ["a refusal of the name", NAME_REFUSAL, `name: ${NAME_REFUSED}`],
  ["a server error", SERVER_ERROR, "Something went wrong"],
];

// The app's QueryClient (src/app.jsx): a failed write reaches the app-wide
// handleError first, which toasts it unless the write's meta.errorHandled is
// set when it runs. The handler is wrapped (it still runs) so a case can show
// it ran before it checks that no toast came from it.
let cacheError;
function renderSave(useSave, options) {
  cacheError = vi.fn(handleError);
  const queryClient = new QueryClient({
    mutationCache: new MutationCache({ onError: cacheError }),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return renderHook(() => useSave(options), { wrapper: Wrapper }).result;
}

// One failed save, told by the time the caller's onError has run (it runs
// after the app-wide handler).
async function failOnce(result, payload, request, rejection) {
  request.mockRejectedValueOnce(rejection);
  const onError = vi.fn();
  act(() => result.current.mutate(payload, { onError }));
  await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
  expect(onError.mock.calls[0][0]).toBe(rejection);
}

beforeEach(() => {
  vi.clearAllMocks();
});

// The page says per failure whether it shows it itself (pageReports). The
// app-wide toast stays out of exactly those, so a failure is told once:
// by the page, or else by the app-wide toast.
describe.each(SAVES)(
  "Webhooks: %s a webhook that fails",
  (_label, useSave, payload, request) => {
    it.each(FAILURES)(
      "leaves %s that the page shows to the page, with no app-wide toast",
      async (_failure, rejection) => {
        const pageReports = vi.fn(() => true);
        const result = renderSave(useSave, { pageReports });
        await failOnce(result, payload, request, rejection);
        expect(request).toHaveBeenCalledTimes(1);
        expect(pageReports).toHaveBeenCalledWith(rejection);
        expect(cacheError).toHaveBeenCalledTimes(1);
        expect(cacheError.mock.calls[0][0]).toBe(rejection);
        expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
      },
    );

    it.each(FAILURES)(
      "keeps the app-wide toast, once, for %s that the page does not show",
      async (_failure, rejection, toast) => {
        const result = renderSave(useSave, { pageReports: () => false });
        await failOnce(result, payload, request, rejection);
        expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
        expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(toast, {
          variant: "error",
        });
      },
    );

    it("keeps the app-wide toast when no page says it shows failures", async () => {
      const result = renderSave(useSave);
      await failOnce(result, payload, request, SERVER_ERROR);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    it("asks the page about each failure on its own", async () => {
      const pageReports = vi
        .fn()
        .mockReturnValueOnce(true)
        .mockReturnValueOnce(false);
      const result = renderSave(useSave, { pageReports });
      await failOnce(result, payload, request, SERVER_ERROR);
      expect(cacheError).toHaveBeenCalledTimes(1);
      expect(cacheError.mock.calls[0][0]).toBe(SERVER_ERROR);
      expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
      await failOnce(result, payload, request, SERVER_ERROR);
      expect(cacheError).toHaveBeenCalledTimes(2);
      expect(pageReports).toHaveBeenCalledTimes(2);
      expect(mocks.enqueueSnackbar).toHaveBeenCalledTimes(1);
    });

    // Over a real network the request is still open when the hook re-renders
    // the pending save; the failure is then told as it is at once.
    it("leaves a failure the page shows to the page when it comes back after the save was pending", async () => {
      let fail;
      request.mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            fail = reject;
          }),
      );
      const result = renderSave(useSave, { pageReports: () => true });
      const onError = vi.fn();
      act(() => result.current.mutate(payload, { onError }));
      await waitFor(() => expect(result.current.isPending).toBe(true));
      await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
      await act(async () => fail(SERVER_ERROR));
      await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
      expect(cacheError).toHaveBeenCalledTimes(1);
      expect(cacheError.mock.calls[0][0]).toBe(SERVER_ERROR);
      expect(mocks.enqueueSnackbar).not.toHaveBeenCalled();
    });
  },
);
