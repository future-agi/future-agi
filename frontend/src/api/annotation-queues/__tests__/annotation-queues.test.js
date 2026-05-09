import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import {
  annotationQueueEndpoints,
  annotationQueueKeys,
  queueItemKeys,
  annotateKeys,
  automationRuleKeys,
  useCreateAutomationRule,
  useAnnotateDetail,
  useCompleteItem,
  useSubmitAnnotations,
} from "../annotation-queues";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function createQueryWrapper(queryClient = createTestQueryClient()) {
  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }

  QueryWrapper.propTypes = {
    children: PropTypes.node,
  };

  return QueryWrapper;
}

describe("Annotation Queues API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("queue endpoints", () => {
    it("has correct list endpoint", () => {
      expect(annotationQueueEndpoints.list).toBe(
        "/model-hub/annotation-queues/",
      );
    });

    it("generates correct detail endpoint", () => {
      expect(annotationQueueEndpoints.detail("q-123")).toBe(
        "/model-hub/annotation-queues/q-123/",
      );
    });

    it("generates correct restore endpoint", () => {
      expect(annotationQueueEndpoints.restore("q-123")).toBe(
        "/model-hub/annotation-queues/q-123/restore/",
      );
    });

    it("generates correct updateStatus endpoint", () => {
      expect(annotationQueueEndpoints.updateStatus("q-123")).toBe(
        "/model-hub/annotation-queues/q-123/update-status/",
      );
    });
  });

  describe("queue query keys", () => {
    it("has correct all key", () => {
      expect(annotationQueueKeys.all).toEqual(["annotation-queues"]);
    });

    it("generates list key with filters", () => {
      const filters = { status: "active", page: 2 };
      expect(annotationQueueKeys.list(filters)).toEqual([
        "annotation-queues",
        "list",
        filters,
      ]);
    });

    it("generates detail key", () => {
      expect(annotationQueueKeys.detail("q-123")).toEqual([
        "annotation-queues",
        "detail",
        "q-123",
      ]);
    });
  });

  describe("queue item keys", () => {
    it("generates all key for queue", () => {
      expect(queueItemKeys.all("q-1")).toEqual(["queue-items", "q-1"]);
    });

    it("generates list key with filters", () => {
      const filters = { status: "pending" };
      expect(queueItemKeys.list("q-1", filters)).toEqual([
        "queue-items",
        "q-1",
        "list",
        filters,
      ]);
    });
  });

  describe("annotate keys", () => {
    it("generates detail key", () => {
      expect(annotateKeys.detail("q-1", "item-1")).toEqual([
        "annotate-detail",
        "q-1",
        "item-1",
      ]);
    });

    it("generates annotator-scoped detail key", () => {
      expect(annotateKeys.detail("q-1", "item-1", "user-1")).toEqual([
        "annotate-detail",
        "q-1",
        "item-1",
        "user-1",
      ]);
    });

    it("generates nextItem key", () => {
      expect(annotateKeys.nextItem("q-1")).toEqual([
        "annotate-next-item",
        "q-1",
      ]);
    });

    it("generates annotations key", () => {
      expect(annotateKeys.annotations("q-1", "item-1")).toEqual([
        "item-annotations",
        "q-1",
        "item-1",
      ]);
    });
  });

  describe("automation rule keys", () => {
    it("generates all key for queue", () => {
      expect(automationRuleKeys.all("q-1")).toEqual([
        "automation-rules",
        "q-1",
      ]);
    });

    it("generates list key for queue", () => {
      expect(automationRuleKeys.list("q-1")).toEqual([
        "automation-rules",
        "q-1",
        "list",
      ]);
    });
  });

  describe("useAnnotateDetail", () => {
    it("passes annotator_id when an annotator is selected", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { annotations: [] } },
      });

      const { result } = renderHook(
        () =>
          useAnnotateDetail("queue-1", "item-1", {
            annotatorId: "user-2",
          }),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/item-1/annotate-detail/",
        { params: { annotator_id: "user-2" } },
      );
    });
  });

  describe("useSubmitAnnotations", () => {
    it("invalidates item annotation history after submit", async () => {
      axios.post.mockResolvedValueOnce({ data: { result: { submitted: 3 } } });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useSubmitAnnotations(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        annotations: [{ label_id: "label-1", value: 45 }],
        notes: "checked",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/annotations/submit/",
          {
            annotations: [{ label_id: "label-1", value: 45 }],
            notes: "checked",
          },
        );
      });

      await waitFor(() => {
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.detail("queue-1", "item-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.annotations("queue-1", "item-1"),
        });
      });
    });
  });

  describe("useCompleteItem", () => {
    it("invalidates annotate detail and annotation history after complete", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { next_item: null } },
      });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useCompleteItem(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
      });

      await waitFor(() => {
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.detail("queue-1", "item-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.annotations("queue-1", "item-1"),
        });
      });
    });
  });

  describe("useCreateAutomationRule", () => {
    it("surfaces backend automation_rules entitlement reasons in the snackbar", async () => {
      axios.post.mockRejectedValueOnce({
        response: {
          status: 403,
          data: {
            status: false,
            result: "automation_rules limit reached for this workspace",
          },
        },
      });

      const { result } = renderHook(() => useCreateAutomationRule(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        queueId: "queue-1",
        name: "Quota blocked rule",
        source_type: "trace",
        conditions: {},
        enabled: true,
      });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "automation_rules limit reached for this workspace",
          { variant: "error" },
        );
      });
    });
  });
});
