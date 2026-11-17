import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { handleError } from "src/utils/queryErrorHandler";
import { resetAllStores } from "src/utils/zustand";
import { newHttpActionKey } from "src/sections/common/Runs/httpResendCopy";
import * as resultApi from "./run-results";
const { rerunResultCells, useRerunResultCells } = resultApi;

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    develop: {
      experiment: {
        reRunExperimentCell: (id) => `/experiments/${id}/rerun-cells/`,
      },
    },
  },
}));
vi.mock("src/sections/common/Runs/httpResendCopy", async (original) => ({
  ...(await original()),
  newHttpActionKey: vi.fn(),
}));
const clients = [];
const http = (id = "h", experiment = "experiment-a") => ({
  run_id: id,
  experiment_id: experiment,
  output_column_id: `output-${id}`,
  source: "http",
  task_kind: "external",
  status: "Completed",
});
const cell = (run = http(), exampleId = "example-a") => ({ run, exampleId });
const selector = (id = "h", example = "example-a") => ({
  column_id: `output-${id}`,
  run_id: id,
  example_id: example,
});
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
function mount(options = {}, sharedClient, datasetId = "dataset") {
  const client =
    sharedClient ??
    new QueryClient({
      defaultOptions: { mutations: { retry: false } },
      mutationCache: new MutationCache({ onError: handleError }),
    });
  if (!sharedClient) clients.push(client);
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(() => useRerunResultCells(datasetId, options), { wrapper }),
    client,
  };
}
const request = (result, cells) => act(() => result.current.request(cells));
const send = async (result) => {
  await act(async () => {
    await result.current.confirmProps.action.props.onClick();
  });
};

beforeEach(() => {
  resetAllStores();
  vi.clearAllMocks();
  axios.post.mockReset().mockResolvedValue({ data: { status: true } });
  let number = 0;
  newHttpActionKey.mockImplementation(() => `action-${++number}`);
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  resetAllStores();
});

describe("HTTP selected-cell transport", () => {
  it("sends successful canonical HTTP cells with explicit acknowledgement and exact key", async () => {
    await rerunResultCells([cell()], {
      acknowledged: true,
      key: "explicit-action",
    });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(
      "/experiments/experiment-a/rerun-cells/",
      {
        cells: [selector()],
        failed_only: false,
        acknowledge_http_side_effects: true,
      },
      { headers: { "Idempotency-Key": "explicit-action" } },
    );
  });
  it.each([
    {},
    { acknowledged: false, key: "key" },
    { acknowledged: true },
    { acknowledged: true, key: "" },
    { acknowledged: true, key: "   " },
  ])(
    "refuses HTTP without both consent and a nonempty key (%j)",
    async (action) => {
      await expect(rerunResultCells([cell()], action)).rejects.toThrow(
        /acknowledgement|key/i,
      );
      expect(axios.post).not.toHaveBeenCalled();
    },
  );
  it.each([
    { target_type: "http" },
    { config: { kind: "http" } },
    { source: { kind: "http" } },
    { kind: "http" },
  ])("retains legacy HTTP consent checks (%j)", async (marker) => {
    await expect(
      rerunResultCells([cell({ ...http(), source: undefined, ...marker })]),
    ).rejects.toThrow();
    expect(axios.post).not.toHaveBeenCalled();
  });
  it.each(["native", "external"])(
    "does not guess %s into HTTP and preserves the two-argument prompt contract",
    async (source) => {
      await rerunResultCells([cell({ ...http(), source })]);
      expect(axios.post).toHaveBeenCalledTimes(1);
      expect(axios.post).toHaveBeenCalledWith(
        "/experiments/experiment-a/rerun-cells/",
        {
          cells: [selector()],
          failed_only: true,
        },
      );
    },
  );
  it("partitions mixed HTTP/native columns and experiments without adding any rows", async () => {
    await rerunResultCells(
      [
        cell(),
        cell({ ...http("p"), source: "native" }, "example-p"),
        cell(http("b", "experiment-b"), "example-b"),
      ],
      { acknowledged: true, key: "mixed" },
    );
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(axios.post).toHaveBeenNthCalledWith(
      1,
      "/experiments/experiment-a/rerun-cells/",
      {
        cells: [selector()],
        failed_only: false,
        acknowledge_http_side_effects: true,
      },
      { headers: { "Idempotency-Key": "mixed" } },
    );
    expect(axios.post).toHaveBeenNthCalledWith(
      2,
      "/experiments/experiment-a/rerun-cells/",
      {
        cells: [selector("p", "example-p")],
        failed_only: true,
      },
    );
    expect(axios.post).toHaveBeenNthCalledWith(
      3,
      "/experiments/experiment-b/rerun-cells/",
      {
        cells: [selector("b", "example-b")],
        failed_only: false,
        acknowledge_http_side_effects: true,
      },
      { headers: { "Idempotency-Key": "mixed" } },
    );
  });
});

describe("shared result confirmation envelope", () => {
  it("creates no key or write before Confirm; Cancel, backdrop and Escape discard only unsent actions", () => {
    const { result } = mount();
    for (const reason of [undefined, "backdropClick", "escapeKeyDown"]) {
      request(result, [cell()]);
      expect(result.current.confirmProps.content).toMatch(
        /already have processed/,
      );
      expect(axios.post).not.toHaveBeenCalled();
      expect(newHttpActionKey).not.toHaveBeenCalled();
      act(() => result.current.confirmProps.onClose({}, reason));
      expect(result.current.confirmProps).toBeNull();
    }
  });
  it("keeps prompt immediate and preserves callback variables and invalidations", async () => {
    const onSuccess = vi.fn();
    const { result, client } = mount({ onSuccess });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const cells = [cell({ ...http(), source: "native" })];
    request(result, cells);
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(onSuccess.mock.calls[0][1]).toBe(cells);
    expect(result.current.confirmProps).toBeNull();
    expect(newHttpActionKey).not.toHaveBeenCalled();
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["develop", "run-results", "dataset"],
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["experiment"] });
  });
  it("preserves prompt error identity and mutation lifecycle inputs", async () => {
    const error = { statusCode: 403, detail: "Not admitted" };
    const onError = vi.fn();
    const onSettled = vi.fn();
    const context = { original: true };
    const { result, client } = mount({
      onError,
      onSettled,
      onMutate: () => context,
    });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const cells = [cell({ ...http(), source: "native" })];
    axios.post.mockRejectedValueOnce(error);
    request(result, cells);
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError.mock.calls[0].slice(0, 3)).toEqual([error, cells, context]);
    await waitFor(() => expect(onSettled).toHaveBeenCalledTimes(1));
    expect(onSettled.mock.calls[0].slice(0, 4)).toEqual([
      undefined,
      error,
      cells,
      context,
    ]);
    expect(invalidate).not.toHaveBeenCalled();
  });
  it("does not automatically retry confirmed HTTP even when the caller enables retries", async () => {
    axios.post.mockRejectedValueOnce({ statusCode: 503 });
    const { result } = mount({ retry: 2, retryDelay: 0 });
    request(result, [cell()]);
    await send(result);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(result.current.confirmProps.action.props.children).toBe(
      "Check again",
    );
  });
  it("rejects bypassing confirmation through mutateAsync", async () => {
    const { result } = mount();
    await act(async () => {
      await expect(result.current.mutateAsync([cell()])).rejects.toThrow(
        /acknowledgement|key/i,
      );
    });
    expect(axios.post).not.toHaveBeenCalled();
  });
  it("freezes selectors and experiment groups while confirmation is open", async () => {
    const { result } = mount();
    const run = http();
    const cells = [cell(run)];
    request(result, cells);
    run.run_id = "changed";
    run.output_column_id = "changed";
    run.experiment_id = "changed";
    run.source = "native";
    cells[0].exampleId = "changed";
    cells.push(cell(http("unselected")));
    await send(result);
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(
      "/experiments/experiment-a/rerun-cells/",
      {
        cells: [selector()],
        failed_only: false,
        acknowledge_http_side_effects: true,
      },
      { headers: { "Idempotency-Key": "action-1" } },
    );
  });
  it("guards duplicate clicks synchronously, waits for every outcome and recovers only unknown groups", async () => {
    const slow = deferred();
    axios.post
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockReturnValueOnce(slow.promise);
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const { result } = mount({ onSuccess, onError });
    request(result, [cell(), cell(http("b", "experiment-b"))]);
    let first;
    act(() => {
      const click = result.current.confirmProps.action.props.onClick;
      first = click();
      click();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(onError).not.toHaveBeenCalled();
    expect(result.current.confirmProps.closeDisabled).toBe(true);
    act(() => result.current.confirmProps.onClose());
    expect(result.current.confirmProps).not.toBeNull();
    await act(async () => {
      slow.resolve({ data: { status: true } });
      await first;
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onSuccess).not.toHaveBeenCalled();
    const original = axios.post.mock.calls[0];
    act(() => result.current.confirmProps.onClose());
    request(result, [cell(http("new-selection"))]);
    expect(result.current.confirmProps.action.props.children).toBe(
      "Check again",
    );
    await send(result);
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(axios.post.mock.calls[2]).toEqual(original);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
    expect(onSuccess).toHaveBeenCalledTimes(1);
    request(result, [cell()]);
    await send(result);
    expect(axios.post.mock.calls[3][2].headers["Idempotency-Key"]).toBe(
      "action-2",
    );
  });
  it("never replays unknown unkeyed native groups while recovering keyed HTTP groups", async () => {
    axios.post
      .mockRejectedValueOnce({ statusCode: 500 })
      .mockRejectedValueOnce(new Error("connection lost"));
    const onError = vi.fn();
    const { result } = mount({ onError });
    request(result, [cell(), cell({ ...http("p"), source: "native" })]);
    await send(result);
    expect(result.current.confirmProps.content).toMatch(
      /will not be sent again/,
    );
    await send(result);
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(axios.post.mock.calls[2]).toEqual(axios.post.mock.calls[0]);
    expect(result.current.confirmProps.action.props.disabled).toBe(true);
    expect(result.current.confirmProps.content).toMatch(/Check.*results/i);
    expect(onError).toHaveBeenCalledTimes(2);
    act(() => result.current.confirmProps.onClose());
    request(result, [cell(http("replacement"))]);
    expect(result.current.confirmProps.action.props.disabled).toBe(true);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });
  it("does not resend refused groups and permits a fresh explicit action after all outcomes settle", async () => {
    axios.post
      .mockRejectedValueOnce({ statusCode: 403 })
      .mockRejectedValueOnce({ statusCode: 502 });
    const { result } = mount();
    request(result, [cell(), cell(http("b", "experiment-b"))]);
    await send(result);
    await send(result);
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(axios.post.mock.calls[2]).toEqual(axios.post.mock.calls[1]);
    expect(result.current.confirmProps).toBeNull();
    request(result, [cell()]);
    await send(result);
    expect(newHttpActionKey).toHaveBeenCalledTimes(2);
  });
});

describe("r4 shared ownership and reset regressions", () => {
  it("recovers the original key/body from another already-mounted caller", async () => {
    const first = mount();
    const second = mount({}, first.client);
    axios.post.mockRejectedValueOnce({ statusCode: 503 });
    request(first.result, [cell()]);
    await send(first.result);
    const original = axios.post.mock.calls[0];
    act(() => first.result.current.confirmProps.onClose());
    request(second.result, [cell(http("different"))]);
    expect(second.result.current.confirmProps.action.props.children).toBe(
      "Check again",
    );
    await send(second.result);
    expect(axios.post.mock.calls[1]).toEqual(original);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });
  it("retains successful-group skipsets through selection-hook unmount/remount", async () => {
    const first = mount();
    axios.post
      .mockResolvedValueOnce({ data: { status: true } })
      .mockRejectedValueOnce({ statusCode: 502 });
    request(first.result, [cell(), cell(http("b", "experiment-b"))]);
    await send(first.result);
    const originalUnknown = axios.post.mock.calls[1];
    first.unmount();
    const remounted = mount({}, first.client);
    request(remounted.result, [cell(http("changed"))]);
    await send(remounted.result);
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(axios.post.mock.calls[2]).toEqual(originalUnknown);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });
  it("shares the synchronous busy guard across instances and keeps datasets isolated", async () => {
    const slow = deferred();
    axios.post.mockReturnValueOnce(slow.promise);
    const first = mount();
    const second = mount({}, first.client);
    const otherDataset = mount({}, first.client, "other-dataset");
    request(first.result, [cell()]);
    request(second.result, [cell()]);
    let pending;
    act(() => {
      pending = first.result.current.confirmProps.action.props.onClick();
      second.result.current.confirmProps.action.props.onClick();
      second.result.current.confirmProps.onClose();
    });
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(second.result.current.confirmProps.closeDisabled).toBe(true);
    expect(second.result.current.confirmProps.action.props.disabled).toBe(true);
    request(otherDataset.result, [cell(http("other"))]);
    await send(otherDataset.result);
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(newHttpActionKey).toHaveBeenCalledTimes(2);
    await act(async () => {
      slow.resolve({ data: { status: true } });
      await pending;
    });
    expect(first.result.current.confirmProps).toBeNull();
    expect(second.result.current.confirmProps).toBeNull();
  });
  it("never replays an unknown native group after its original caller unmounts", async () => {
    const first = mount();
    axios.post
      .mockRejectedValueOnce({ statusCode: 503 })
      .mockRejectedValueOnce(new Error("lost"));
    request(first.result, [cell(), cell({ ...http("p"), source: "native" })]);
    await send(first.result);
    first.unmount();
    const second = mount({}, first.client);
    request(second.result, [cell(http("new"))]);
    await send(second.result);
    expect(axios.post).toHaveBeenCalledTimes(3);
    expect(axios.post.mock.calls[2]).toEqual(axios.post.mock.calls[0]);
    expect(second.result.current.confirmProps.content).toMatch(
      /will not be sent again/,
    );
    expect(second.result.current.confirmProps.action.props.disabled).toBe(true);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
  });
  it("reset drops held actions and stale confirmation handlers cannot submit a new session's action", async () => {
    const first = mount();
    axios.post.mockRejectedValueOnce({ statusCode: 503 });
    request(first.result, [cell()]);
    await send(first.result);
    const staleConfirm = first.result.current.confirmProps.action.props.onClick;
    act(() => {
      resetAllStores();
      first.client.clear();
    });
    expect(first.result.current.confirmProps).toBeNull();
    const next = mount({}, first.client);
    request(next.result, [cell(http("next-session"))]);
    await act(async () => {
      await staleConfirm();
    });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(newHttpActionKey).toHaveBeenCalledTimes(1);
    await send(next.result);
    expect(axios.post.mock.calls[1][1].cells).toEqual([
      selector("next-session"),
    ]);
    expect(axios.post.mock.calls[1][2].headers["Idempotency-Key"]).toBe(
      "action-2",
    );
  });
  it.each(["success", "refusal", "unknown"])(
    "ignores a late %s after reset without clearing a new action",
    async (outcome) => {
      const slow = deferred();
      axios.post.mockReturnValueOnce(slow.promise);
      const callbacks = {
        onSuccess: vi.fn(),
        onError: vi.fn(),
        onSettled: vi.fn(),
      };
      const first = mount(callbacks);
      const invalidate = vi.spyOn(first.client, "invalidateQueries");
      request(first.result, [cell()]);
      let pending;
      act(() => {
        pending = first.result.current.confirmProps.action.props.onClick();
      });
      await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
      act(() => {
        resetAllStores();
        first.client.clear();
      });
      const next = mount({}, first.client);
      request(next.result, [cell(http("next-session"))]);
      await act(async () => {
        if (outcome === "success") slow.resolve({ data: { status: true } });
        else
          slow.reject({
            statusCode: outcome === "refusal" ? 400 : 503,
            result: "Old session refusal",
          });
        await pending;
      });
      expect(callbacks.onSuccess).not.toHaveBeenCalled();
      expect(callbacks.onError).not.toHaveBeenCalled();
      expect(callbacks.onSettled).not.toHaveBeenCalled();
      expect(invalidate).not.toHaveBeenCalled();
      expect(enqueueSnackbar).not.toHaveBeenCalled();
      expect(next.result.current.confirmProps.action.props.children).toBe(
        "Send again",
      );
      await send(next.result);
      expect(axios.post.mock.calls[1][1].cells).toEqual([
        selector("next-session"),
      ]);
    },
  );
  it("does not send after reset while asynchronous onMutate is still pending", async () => {
    const gate = deferred();
    const onMutate = vi.fn(() => gate.promise);
    const onError = vi.fn();
    const onSettled = vi.fn();
    const first = mount({ onMutate, onError, onSettled });
    request(first.result, [cell()]);
    let pending;
    act(() => {
      pending = first.result.current.confirmProps.action.props.onClick();
    });
    await waitFor(() => expect(onMutate).toHaveBeenCalledTimes(1));
    act(() => resetAllStores());
    await act(async () => {
      gate.resolve({ context: true });
      await pending;
    });
    expect(axios.post).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
    expect(onSettled).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    expect(first.result.current.confirmProps).toBeNull();
  });
});

describe("r4 visible failure notifications with the real MutationCache handler", () => {
  it("keeps a pending prompt refusal visible when another caller opens HTTP confirmation", async () => {
    const slow = deferred();
    const onError = vi.fn();
    const native = mount({ onError });
    const other = mount({}, native.client);
    const error = {
      statusCode: 400,
      result: "The selected run is no longer available.",
    };
    axios.post.mockReturnValueOnce(slow.promise);
    request(native.result, [cell({ ...http("prompt"), source: "native" })]);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    request(other.result, [cell()]);
    expect(other.result.current.confirmProps).not.toBeNull();
    expect(newHttpActionKey).not.toHaveBeenCalled();
    await act(async () => {
      slow.reject(error);
    });
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(error.result, {
      variant: "error",
    });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(other.result.current.confirmProps.action.props.children).toBe(
      "Send again",
    );
  });
  it.each([
    { statusCode: 400, result: "The selected run is no longer available." },
    {
      statusCode: 403,
      code: "permission_denied",
      result: "You cannot rerun this experiment.",
    },
  ])(
    "keeps the original safe refusal visible after settled action clear (%j)",
    async (error) => {
      const onSuccess = vi.fn();
      const { result } = mount({ onSuccess });
      axios.post.mockRejectedValueOnce(error);
      request(result, [cell()]);
      await send(result);
      expect(result.current.confirmProps).toBeNull();
      expect(onSuccess).not.toHaveBeenCalled();
      expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
      expect(enqueueSnackbar.mock.calls[0][0]).toContain(error.result);
      expect(enqueueSnackbar.mock.calls[0][0]).toMatch(
        /0.*confirmed.*1.*unconfirmed/i,
      );
      expect(enqueueSnackbar.mock.calls[0][1]).toMatchObject({
        variant: "error",
      });
    },
  );
  it("reports mixed partial success once, including the refusal and group counts", async () => {
    const onSuccess = vi.fn();
    const { result } = mount({ onSuccess });
    axios.post
      .mockResolvedValueOnce({ data: { status: true } })
      .mockRejectedValueOnce({
        statusCode: 400,
        result: "The selected run is no longer available.",
      });
    request(result, [cell(), cell(http("b", "experiment-b"))]);
    await send(result);
    expect(result.current.confirmProps).toBeNull();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar.mock.calls[0][0]).toMatch(
      /1.*confirmed.*1.*unconfirmed/i,
    );
    expect(enqueueSnackbar.mock.calls[0][0]).toContain(
      "The selected run is no longer available.",
    );
    expect(axios.post).toHaveBeenCalledTimes(2);
  });
  it("does not display arbitrary server internals or raw nested payloads", async () => {
    const { result } = mount();
    axios.post.mockRejectedValueOnce({
      statusCode: 503,
      result: { details: "Traceback PRIVATE_SENTINEL" },
    });
    request(result, [cell()]);
    await send(result);
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar.mock.calls[0][0]).not.toContain("PRIVATE_SENTINEL");
    expect(enqueueSnackbar.mock.calls[0][0]).toMatch(/Check.*results/i);
  });
});
