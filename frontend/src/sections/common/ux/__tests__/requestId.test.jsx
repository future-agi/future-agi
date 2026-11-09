import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));
vi.mock("../toast", () => ({ notify: vi.fn() }));

import axios from "src/utils/axios";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import ErrorStateComponent from "../ErrorState";

// Use the runtime prop contract instead of ErrorState's string-like JSDoc inference.
/** @typedef {import("prop-types").InferProps<typeof ErrorStateComponent.propTypes>} ErrorStateProps */
const ErrorState = /** @type {React.ComponentType<ErrorStateProps>} */ (
  /** @type {unknown} */ (ErrorStateComponent)
);

beforeEach(() =>
  setLaunchOverrideForTest({ launch: true, madeReal: ["errors.requestId"] }),
);

// Axios keeps these registered callbacks at runtime but omits them from its public type.
/** @typedef {{ rejected: (error: unknown) => Promise<unknown> }} ResponseHandler */
const responseInterceptors =
  /** @type {typeof axios.interceptors.response & { handlers: ResponseHandler[] }} */ (
    axios.interceptors.response
  );
const reject = responseInterceptors.handlers.find(
  (handler) => handler.rejected,
).rejected;

const apiError = (headers = {}, data = {}) => ({
  config: { url: "/model-hub/develops/get-datasets/", method: "get" },
  response: {
    status: 403,
    headers,
    data: { message: "Workspace access denied", ...data },
  },
});

// These non-auth 403 and network fixtures reach Axios' customError rejection.
/**
 * @typedef {object} NormalizedError
 * @property {number | undefined} statusCode
 * @property {{ "x-request-id": string | undefined }} responseHeaders
 */
async function normalized(error) {
  return /** @type {Promise<NormalizedError>} */ (
    reject(error).catch((result) => result)
  );
}

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("request id on API error states", () => {
  it("keeps an unclaimed request id action hidden even with a readable header", () => {
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    render(<ErrorState error={apiError({ "x-request-id": "readable-id" })} />);
    expect(
      screen.queryByRole("button", { name: "Copy request id" }),
    ).toBeNull();
  });

  it("hides caller and normalized ids until the surface is claimed", async () => {
    setLaunchOverrideForTest({ launch: true, madeReal: [] });
    const error = await normalized(apiError({ "x-request-id": "wire-id" }));
    const { rerender } = render(<ErrorState requestId="caller-id" />);
    expect(screen.queryByText("Request id")).toBeNull();
    rerender(<ErrorState error={error} />);
    expect(screen.queryByText("Request id")).toBeNull();
    expect(screen.getByText("Workspace access denied")).toBeInTheDocument();
  });

  it("keeps prototype body ids and retry behavior", () => {
    setLaunchOverrideForTest({ launch: false });
    const retry = vi.fn();
    render(
      <ErrorState error={{ requestId: "prototype-id" }} onRetry={retry} />,
    );
    expect(screen.getByText("Request id")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("retains only the readable correlation header and copies that exact id", async () => {
    const id = "60307c61-0faf-48f5-8b9e-b9307cb401ca";
    const error = await normalized(
      apiError({ "x-request-id": id, authorization: "not-for-the-ui" }),
    );
    expect(error.responseHeaders).toEqual({ "x-request-id": id });
    expect(error.statusCode).toBe(403);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ErrorState thing="datasets" error={error} />);
    expect(screen.getByText("Workspace access denied")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy request id" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledExactlyOnceWith(id));
  });

  it("does not treat body-supplied metadata as a readable response header", async () => {
    const error = await normalized(
      apiError(
        {},
        {
          responseHeaders: { "x-request-id": "body-only" },
          response: { headers: { "x-request-id": "nested-body-only" } },
        },
      ),
    );
    expect(error.responseHeaders).toEqual({ "x-request-id": undefined });
    render(<ErrorState error={error} />);
    expect(
      screen.queryByRole("button", { name: "Copy request id" }),
    ).toBeNull();
  });

  it("hides the copy action for a network failure with no response", async () => {
    const error = await normalized({
      message: "Network Error",
      code: "ERR_NETWORK",
    });
    render(<ErrorState error={error} />);
    expect(
      screen.queryByRole("button", { name: "Copy request id" }),
    ).toBeNull();
    expect(
      screen.getByText("The server didn't answer. Check your connection."),
    ).toBeInTheDocument();
  });

  it("still accepts a raw Axios error header and a caller-supplied id", () => {
    const { rerender } = render(
      <ErrorState error={apiError({ "x-request-id": "raw-id" })} />,
    );
    expect(
      screen.getByRole("button", { name: "Copy request id" }),
    ).toBeInTheDocument();
    rerender(<ErrorState requestId="caller-id" />);
    expect(
      screen.getByRole("button", { name: "Copy request id" }),
    ).toBeInTheDocument();
  });
});
