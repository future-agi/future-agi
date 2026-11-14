import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDeleteWebhook, useUpdateWebhook } from "./useWebhooks";
import axios, { endpoints } from "src/utils/axios";

const { mockInvalidate, mockUseMutation } = vi.hoisted(() => ({
  mockInvalidate: vi.fn(),
  mockUseMutation: vi.fn((options) => options),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  useMutation: mockUseMutation,
  useQueryClient: () => ({ invalidateQueries: mockInvalidate }),
  keepPreviousData: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { patch: vi.fn(), delete: vi.fn() },
  endpoints: {
    gateway: {
      webhooks: {
        update: (id) => `/webhooks/${id}/`,
        delete: (id) => `/webhooks/${id}/`,
      },
    },
  },
}));

describe("webhook mutations", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends only is_active to the update endpoint when enabling", async () => {
    axios.patch.mockResolvedValue({ data: { result: { id: "webhook-1" } } });
    const mutation = useUpdateWebhook();
    await mutation.mutationFn({ id: "webhook-1", is_active: true });
    expect(axios.patch).toHaveBeenCalledWith(
      endpoints.gateway.webhooks.update("webhook-1"),
      {
        is_active: true,
      },
    );
    mutation.onSuccess();
    expect(mockInvalidate).toHaveBeenCalledWith({
      queryKey: ["agentcc-webhooks"],
    });
  });

  it("invalidates webhook delivery events after deletion", async () => {
    axios.delete.mockResolvedValue({ data: { result: {} } });
    const mutation = useDeleteWebhook();
    await mutation.mutationFn("webhook-1");
    expect(axios.delete).toHaveBeenCalledWith(
      endpoints.gateway.webhooks.delete("webhook-1"),
    );
    mutation.onSuccess();
    expect(mockInvalidate).toHaveBeenCalledWith({
      queryKey: ["agentcc-webhook-events"],
    });
  });
});
