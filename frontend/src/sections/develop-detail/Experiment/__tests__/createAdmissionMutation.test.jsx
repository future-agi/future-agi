import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: api,
  endpoints: {
    develop: { experiment: { create: () => "/model-hub/experiments/v2/" } },
  },
}));
vi.mock("src/sections/develop-detail/DataTab/Versions/versionedSource", () => ({
  tableRequestFor: vi.fn(),
}));
import { useCreateExperiment } from "src/api/develop/experiment-create";
import { useCreateNameTaken } from "../useCreateAdmission";
import { RETRY_UNCHANGED } from "../createAdmission";

const response = {
  data: {
    status: true,
    result: { experiment_id: "0b5b7b9e-1111-4c8f-9a8e-0e2f8d1d2c3a" },
  },
};
const error = {
  code: "experiment_admission_unconfirmed",
  detail: "Retry the same submission",
  statusCode: 503,
};
const body = {
  dataset_id: "dataset-a",
  name: "A",
  prompt_config: [{ model: "model-a" }],
};
const clients = [];
const mount = (options = {}) => {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  clients.push(client);
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(() => useCreateExperiment(options), { wrapper }),
    client,
    wrapper,
  };
};
beforeEach(() => vi.clearAllMocks());
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

it("keeps recovery instructions after the normalized Axios rejection", async () => {
  api.post.mockRejectedValue(error);
  const { result } = mount({ retry: 2 });
  await act(async () => {
    await expect(result.current.mutateAsync(body)).rejects.toMatchObject({
      code: error.code,
      message: error.detail,
    });
  });
  expect(api.post).toHaveBeenCalledOnce();
});

it("blocks changed saved payloads and names locally until the identical attempt is confirmed", async () => {
  api.post.mockRejectedValueOnce(error).mockResolvedValue(response);
  const { result } = mount();
  await act(async () => {
    await expect(result.current.mutateAsync(body)).rejects.toThrow(
      error.detail,
    );
  });
  for (const changed of [
    { ...body, prompt_config: [{ model: "model-b" }] },
    { ...body, name: "Another experiment" },
  ]) {
    await act(async () => {
      await expect(result.current.mutateAsync(changed)).rejects.toThrow(
        RETRY_UNCHANGED,
      );
    });
    expect(api.post).toHaveBeenCalledOnce();
  }
  const onSuccess = vi.fn();
  await act(async () => {
    // Object-key order is not part of a JSON request's identity.
    await expect(
      result.current.mutateAsync(
        {
          name: body.name,
          prompt_config: body.prompt_config,
          dataset_id: body.dataset_id,
        },
        { onSuccess },
      ),
    ).resolves.toEqual(response);
  });
  expect(onSuccess).toHaveBeenCalledOnce();
  expect(api.post).toHaveBeenCalledTimes(2);
  await act(async () => {
    await result.current.mutateAsync({
      ...body,
      name: "New after confirmation",
    });
  });
  expect(api.post).toHaveBeenCalledTimes(3);
});

it("snapshots before yielding so later draft edits cannot alter an in-flight request or its retry", async () => {
  api.post.mockRejectedValue(error);
  const { result } = mount();
  const draft = JSON.parse(JSON.stringify(body));
  await act(async () => {
    const attempt = result.current.mutateAsync(draft);
    draft.prompt_config[0].model = "changed-after-click";
    await expect(attempt).rejects.toThrow();
  });
  expect(api.post.mock.calls[0][1]).toEqual(body);
  await act(async () => {
    await expect(result.current.mutateAsync(body)).rejects.toThrow(
      error.detail,
    );
  });
  expect(api.post.mock.calls[1][1]).toEqual(body);
});

it("keeps pending and the synchronous guard through asynchronous success readback", async () => {
  api.post.mockResolvedValue(response);
  let finishReadback;
  const onSuccess = vi.fn(
    () =>
      new Promise((resolve) => {
        finishReadback = resolve;
      }),
  );
  const onSettled = vi.fn();
  const ignored = vi.fn();
  const { result } = mount();
  act(() => result.current.mutate(body, { onSuccess, onSettled }));
  await waitFor(() => expect(onSuccess).toHaveBeenCalledOnce());
  expect(result.current.isPending).toBe(true);
  act(() => result.current.mutate(body, { onSuccess: ignored }));
  expect(api.post).toHaveBeenCalledOnce();
  expect(ignored).not.toHaveBeenCalled();
  await act(async () => finishReadback());
  await waitFor(() => expect(result.current.isPending).toBe(false));
  expect(onSettled).toHaveBeenCalledOnce();
});

it("releases the guard even if a consumer callback throws", async () => {
  api.post.mockResolvedValue(response);
  const { result } = mount();
  await act(async () => {
    await expect(
      result.current.mutateAsync(body, {
        onSuccess: () => {
          throw new Error("Readback failed");
        },
      }),
    ).rejects.toThrow("Readback failed");
  });
  await waitFor(() => expect(result.current.isPending).toBe(false));
  await act(async () => {
    await expect(result.current.mutateAsync(body)).resolves.toEqual(response);
  });
  expect(api.post).toHaveBeenCalledTimes(2);
});

it("limits the taken-name exemption to the matching dataset and saved name, then clears it on confirmation", async () => {
  api.post.mockRejectedValueOnce(error).mockResolvedValue(response);
  const { result, wrapper } = mount();
  const flags = renderHook(
    () => ({
      same: useCreateNameTaken("dataset-a", "A", true),
      otherDataset: useCreateNameTaken("dataset-b", "A", true),
      otherName: useCreateNameTaken("dataset-a", "B", true),
    }),
    { wrapper },
  );
  await act(async () => {
    await expect(result.current.mutateAsync(body)).rejects.toThrow();
  });
  await waitFor(() =>
    expect(flags.result.current).toEqual({
      same: false,
      otherDataset: true,
      otherName: true,
    }),
  );
  await act(async () => {
    await result.current.mutateAsync(body);
  });
  await waitFor(() => expect(flags.result.current.same).toBe(true));
});

it("keeps the saved-name retry after a subsequent network failure", async () => {
  api.post
    .mockRejectedValueOnce(error)
    .mockRejectedValueOnce({ message: "Network error" })
    .mockResolvedValue(response);
  const { result, wrapper } = mount();
  const flag = renderHook(() => useCreateNameTaken("dataset-a", "A", true), {
    wrapper,
  });
  await act(async () => {
    await expect(result.current.mutateAsync(body)).rejects.toThrow(
      error.detail,
    );
  });
  await waitFor(() => expect(flag.result.current).toBe(false));
  await act(async () => {
    await expect(result.current.mutateAsync(body)).rejects.toThrow(
      "Network error",
    );
  });
  await waitFor(() => expect(flag.result.current).toBe(false));
  await act(async () => {
    await expect(result.current.mutateAsync(body)).resolves.toEqual(response);
  });
  await waitFor(() => expect(flag.result.current).toBe(true));
  expect(api.post).toHaveBeenCalledTimes(3);
});

it("does not run drawer effects after unmount while a POST is in flight", async () => {
  let resolve;
  api.post.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const { result, unmount } = mount();
  const onSuccess = vi.fn();
  const onError = vi.fn();
  const onSettled = vi.fn();
  act(() => result.current.mutate(body, { onSuccess, onError, onSettled }));
  await waitFor(() => expect(api.post).toHaveBeenCalledOnce());
  unmount();
  await act(async () => resolve(response));
  expect(onSuccess).not.toHaveBeenCalled();
  expect(onError).not.toHaveBeenCalled();
  expect(onSettled).not.toHaveBeenCalled();
});

it("uses cached recovery after a drawer remount, without allowing a changed request", async () => {
  api.post.mockRejectedValueOnce(error).mockResolvedValue(response);
  const original = mount();
  await act(async () => {
    await expect(original.result.current.mutateAsync(body)).rejects.toThrow();
  });
  original.unmount();
  const remount = renderHook(() => useCreateExperiment(), {
    wrapper: original.wrapper,
  });
  await act(async () => {
    await expect(
      remount.result.current.mutateAsync({
        ...body,
        name: "Renamed after remount",
      }),
    ).rejects.toThrow(RETRY_UNCHANGED);
  });
  await act(async () => {
    await expect(
      remount.result.current.mutateAsync({ ...body, prompt_config: [] }),
    ).rejects.toThrow(RETRY_UNCHANGED);
  });
  expect(api.post).toHaveBeenCalledOnce();
  await act(async () => {
    await expect(remount.result.current.mutateAsync(body)).resolves.toEqual(
      response,
    );
  });
  expect(api.post).toHaveBeenCalledTimes(2);
});
