import React from "react";
import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import * as api from "../annotation-queues";

vi.mock("src/utils/axios", () => ({ default: { post: vi.fn() } }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

describe("Add pair API", () => {
  it("posts pairs to add-pairs and refreshes queue items and summary", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    axios.post.mockResolvedValue({ data: { result: { added: 1 } } });
    const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => api.useAddQueuePairs(), { wrapper });
    const pairs = [{
      source_a: { source_type: "trace", source_id: "trace-a", project_id: "project-1" },
      source_b: { source_type: "trace", source_id: "trace-b", project_id: "project-1" },
    }];
    await result.current.mutateAsync({ queueId: "queue-1", pairs });
    expect(axios.post).toHaveBeenCalledWith(
      "/model-hub/annotation-queues/queue-1/items/add-pairs/", { pairs },
    );
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: api.queueItemKeys.all("queue-1") }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: api.annotationQueueKeys.all });
  });
});
