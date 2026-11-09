import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ projectSupported: false, post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { post: (...args) => h.post(...args) } };
});
vi.mock("src/api/contracts/strictBody", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    acceptedFields: (definition) => {
      const fields = real.acceptedFields(definition);
      return h.projectSupported ? [...fields, "project_id"] : fields;
    },
  };
});

import {
  isRefusedSourceRead,
  useSourceSessionTraces,
} from "src/api/develop/row-source";
import { acceptedFields, bodyDefinitionOf } from "src/api/contracts/strictBody";

const SESSION = "5d6e7f80-0000-4000-8000-000000000002";
const PROJECT_A = "00000000-0000-4000-8000-0000000000b1";
const PROJECT_B = "00000000-0000-4000-8000-0000000000b2";
const clients = [];
const setup = (projectId) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  clients.push(client);
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(
    ({ project }) => useSourceSessionTraces(SESSION, { projectId: project }),
    { wrapper, initialProps: { project: projectId } },
  );
};

beforeEach(() => {
  h.projectSupported = false;
  h.post.mockReset();
  h.post.mockResolvedValue({ data: { result: { response: [], next: false } } });
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("source session request", () => {
  it("sends the record project on every page when the request contract accepts it", async () => {
    h.projectSupported = true;
    h.post.mockImplementation(async (_url, body) => ({
      data: {
        result: {
          response: [{ trace_id: `turn-${body.page_number}` }],
          next: body.page_number === 0,
        },
      },
    }));
    const { result } = setup(PROJECT_A);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await act(async () => result.current.fetchNextPage());
    expect(h.post.mock.calls.map(([, body]) => body)).toEqual([
      { page_number: 0, page_size: 50, project_id: PROJECT_A },
      { page_number: 1, page_size: 50, project_id: PROJECT_A },
    ]);
    expect(h.post.mock.calls.every(([, , config]) => config.signal)).toBe(true);
  });

  it("keeps project-specific pages separate even for the same session id", async () => {
    h.projectSupported = true;
    h.post.mockImplementation(async (_url, body) => ({
      data: {
        result: { response: [{ trace_id: body.project_id }], next: false },
      },
    }));
    const { result, rerender } = setup(PROJECT_A);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    rerender({ project: PROJECT_B });
    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(result.current.data).toEqual([{ trace_id: PROJECT_B }]),
    );
    rerender({ project: PROJECT_A });
    await waitFor(() =>
      expect(result.current.data).toEqual([{ trace_id: PROJECT_A }]),
    );
    expect(h.post).toHaveBeenCalledTimes(2);
  });

  it("does not add a rejected field to the current strict request body", async () => {
    const { result } = setup(PROJECT_A);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const definition = bodyDefinitionOf(
      "post",
      "/tracer/trace-session/{id}/query/",
    );
    const body = h.post.mock.calls[0][1];
    expect(
      Object.keys(body).every((field) =>
        acceptedFields(definition).includes(field),
      ),
    ).toBe(true);
    expect(body).toEqual({ page_number: 0, page_size: 50 });
    expect(h.post.mock.calls[0][2]).not.toHaveProperty("params");
  });

  it("omits an absent project even when the contract accepts one", async () => {
    h.projectSupported = true;
    const { result } = setup(undefined);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.post.mock.calls[0][1]).toEqual({ page_number: 0, page_size: 50 });
  });

  it.each([undefined, {}, { result: {} }])(
    "keeps an empty response safe to render",
    async (data) => {
      h.post.mockResolvedValue({ data });
      const { result } = setup(PROJECT_A);
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data).toEqual([]);
      expect(result.current.hasNextPage).toBe(false);
    },
  );
});

describe("source refusal classification", () => {
  it.each([400, 403, 404, 410])("treats HTTP %s as refused", (statusCode) => {
    expect(isRefusedSourceRead({ statusCode })).toBe(true);
  });
  it.each([
    new Error("Network Error"),
    { code: "ECONNABORTED" },
    { statusCode: 503 },
  ])("does not report a transient failure as an expired source", (error) =>
    expect(isRefusedSourceRead(error)).toBe(false),
  );
});
