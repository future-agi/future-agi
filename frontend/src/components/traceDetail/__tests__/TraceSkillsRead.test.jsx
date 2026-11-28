import React from "react";
import { expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios, { traceSkillsEndpoint } from "src/utils/axios";
import { useTraceSkillsDetail } from "../TraceDetailDrawerV2";

vi.mock("notistack", async (load) => ({ ...(await load()), enqueueSnackbar: vi.fn() }));

const skill = (name) => ({ name, version: 1, span_ids: [name], loads: 1 });
const page = (name, cursor) => ({ data: { result: {
  skills: [skill(name)], failed: [], contexts: [], linked_not_loaded: [],
  coverage: { span_ids_before_first_load: ["root"] }, next_cursor: cursor,
} } });

it("uses one contracted POST projection and follows detail cursors", async () => {
  const post = vi.spyOn(axios, "post").mockResolvedValueOnce(page("one", "cursor-2")).mockResolvedValueOnce(page("two", null));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useTraceSkillsDetail({ traceId: "trace", projectId: "project", enabled: true }), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(traceSkillsEndpoint()).toBe("/tracer/trace/skill-projection/");
  expect(post).toHaveBeenNthCalledWith(1, traceSkillsEndpoint(), {
    projection: "detail", project_id: "project", trace_id: "trace",
  }, expect.objectContaining({ signal: expect.anything() }));
  expect(post.mock.calls[1][1]).toMatchObject({ cursor: "cursor-2", projection: "detail" });
  expect(result.current.data.skills.map((row) => row.name)).toEqual(["one", "two"]);
  post.mockRestore();
});
