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
  extractErrorMessage,
  useCreateAutomationRule,
  useCreateAnnotationQueue,
  useEvaluateRule,
  useBulkReviewItems,
  useCreateDiscussionComment,
  useDeleteDiscussionComment,
  useGetOrCreateDefaultQueue,
  useAnnotateDetail,
  useAssignQueueItems,
  useCompleteItem,
  useItemDiscussion,
  useNextItem,
  useOrgMembersInfinite,
  useQueueItems,
  useQueueItemsForSource,
  useSkipItem,
  useReopenDiscussionThread,
  useRestoreAnnotationQueue,
  useReviewItem,
  useResolveDiscussionThread,
  useSubmitAnnotations,
  useToggleDiscussionReaction,
  useUpdateDiscussionComment,
} from "../annotation-queues";

vi.mock("src/utils/axios", () => ({
  default: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
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

    it("generates correct bulk-review and discussion-comment endpoints", () => {
      expect(annotationQueueEndpoints.bulkReviewItems("q-123")).toBe(
        "/model-hub/annotation-queues/q-123/items/bulk-review/",
      );
      expect(
        annotationQueueEndpoints.discussionComment(
          "q-123",
          "item-1",
          "comment-1",
        ),
      ).toBe(
        "/model-hub/annotation-queues/q-123/items/item-1/discussion/comments/comment-1/",
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

  describe("extractErrorMessage", () => {
    it("reads structured entitlement messages from nested backend errors", () => {
      expect(
        extractErrorMessage(
          {
            error: {
              code: "ENTITLEMENT_LIMIT",
              message:
                "You've reached the 10 annotation queues limit across this organization",
              detail: { current_usage: 10, limit: 10 },
            },
          },
          "Failed",
        ),
      ).toBe(
        "You've reached the 10 annotation queues limit across this organization",
      );
    });
  });

  describe("useCreateAnnotationQueue", () => {
    it("surfaces structured queue limit messages in the snackbar", async () => {
      axios.post.mockRejectedValueOnce({
        error: {
          code: "ENTITLEMENT_LIMIT",
          message:
            "You've reached the 10 annotation queues limit across this organization",
          detail: { current_usage: 10, limit: 10 },
        },
      });

      const { result } = renderHook(() => useCreateAnnotationQueue(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({ name: "Limit blocked queue" });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "You've reached the 10 annotation queues limit across this organization",
          { variant: "error" },
        );
      });
    });
  });

  describe("useGetOrCreateDefaultQueue", () => {
    it("surfaces the backend org-wide queue limit message in the snackbar", async () => {
      const backendMessage =
        "You've reached the 3 annotation queues limit across this organization (5 existing queues; 2 in the current workspace and 3 in other workspaces). Archive unused queues in another workspace or upgrade your plan.";

      axios.post.mockRejectedValueOnce({
        error: {
          code: "ENTITLEMENT_LIMIT",
          message: backendMessage,
          detail: {
            current_usage: 5,
            limit: 3,
            workspace_usage: 2,
            other_workspace_usage: 3,
          },
        },
      });

      const { result } = renderHook(() => useGetOrCreateDefaultQueue(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({ projectId: "project-1" });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith(backendMessage, {
          variant: "error",
        });
      });
      expect(axios.post).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/get-or-create-default/",
        { project_id: "project-1" },
      );
    });
  });

  describe("useRestoreAnnotationQueue", () => {
    it("posts an explicit empty body so the backend accepts the restore", async () => {
      axios.post.mockResolvedValueOnce({ data: { status: true } });

      const { result } = renderHook(() => useRestoreAnnotationQueue(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate("queue-1");

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          annotationQueueEndpoints.restore("queue-1"),
          {},
        );
      });
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

  describe("useQueueItems", () => {
    it("serializes multi-select filters as repeated query params", async () => {
      axios.get.mockResolvedValueOnce({
        data: { results: [], count: 0, current_page: 1, total_pages: 1 },
      });

      renderHook(
        () =>
          useQueueItems("q-1", {
            status: ["pending", "completed"],
            source_type: ["dataset_row", "trace"],
          }),
        { wrapper: createQueryWrapper() },
      );

      await waitFor(() => expect(axios.get).toHaveBeenCalled());

      const requestConfig = axios.get.mock.calls[0][1];
      expect(
        requestConfig.paramsSerializer.serialize(requestConfig.params),
      ).toBe(
        "status=pending&status=completed&source_type=dataset_row&source_type=trace&page=1&limit=25",
      );
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

    it("generates filtered detail key", () => {
      expect(
        annotateKeys.detail("q-1", "item-1", undefined, {
          include_completed: true,
        }),
      ).toEqual([
        "annotate-detail",
        "q-1",
        "item-1",
        { include_completed: true },
      ]);
    });

    it("generates nextItem key", () => {
      expect(annotateKeys.nextItem("q-1")).toEqual([
        "annotate-next-item",
        "q-1",
      ]);
    });

    it("generates filtered nextItem key", () => {
      expect(
        annotateKeys.nextItem("q-1", { review_status: "pending_review" }),
      ).toEqual([
        "annotate-next-item",
        "q-1",
        { review_status: "pending_review" },
      ]);
    });

    it("generates annotations key", () => {
      expect(annotateKeys.annotations("q-1", "item-1")).toEqual([
        "item-annotations",
        "q-1",
        "item-1",
      ]);
    });

    it("generates discussion key", () => {
      expect(annotateKeys.discussion("q-1", "item-1")).toEqual([
        "annotate-discussion",
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

    it("passes include_completed when completed items are visible", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { annotations: [] } },
      });

      const { result } = renderHook(
        () =>
          useAnnotateDetail("queue-1", "item-1", {
            includeCompleted: true,
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
        { params: { include_completed: true } },
      );
    });

    it("does not add completed navigation params unless the UI explicitly opts in", async () => {
      axios.get.mockResolvedValueOnce({
        data: {
          result: {
            item: { id: "item-1", status: "completed" },
            next_item_id: "item-2",
          },
        },
      });

      const { result } = renderHook(
        () => useAnnotateDetail("queue-1", "item-1"),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/item-1/annotate-detail/",
        undefined,
      );
      expect(result.current.data.next_item_id).toBe("item-2");
    });

    it("passes review view mode without requiring a pending-review filter", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { annotations: [] } },
      });

      const { result } = renderHook(
        () =>
          useAnnotateDetail("queue-1", "item-1", {
            viewMode: "review",
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
        { params: { view_mode: "review" } },
      );
    });

    it("passes review mode, selected annotator, and review status together", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { annotations: [] } },
      });

      const { result } = renderHook(
        () =>
          useAnnotateDetail("queue-1", "item-1", {
            viewMode: "review",
            reviewStatus: "pending_review",
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
        {
          params: {
            annotator_id: "user-2",
            view_mode: "review",
            review_status: "pending_review",
          },
        },
      );
    });
  });

  describe("useNextItem", () => {
    it("refetches on mount instead of trusting cached next item data", async () => {
      const queryClient = createTestQueryClient();
      queryClient.setQueryData(annotateKeys.nextItem("queue-1"), {
        data: { result: { item: { id: "old-item" } } },
      });
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "new-item" } } },
      });

      const { result } = renderHook(() => useNextItem("queue-1"), {
        wrapper: createQueryWrapper(queryClient),
      });

      expect(result.current.data).toEqual({ id: "old-item" });

      await waitFor(() => {
        expect(axios.get).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/next-item/",
          undefined,
        );
      });
      await waitFor(() => {
        expect(result.current.data).toEqual({ id: "new-item" });
      });
    });

    it("passes review mode filters", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "item-1" } } },
      });

      const { result } = renderHook(
        () => useNextItem("queue-1", { reviewStatus: "pending_review" }),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/next-item/",
        { params: { review_status: "pending_review" } },
      );
    });

    it("passes annotator mode filters", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "item-1" } } },
      });

      const { result } = renderHook(
        () => useNextItem("queue-1", { excludeReviewStatus: "pending_review" }),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/next-item/",
        { params: { exclude_review_status: "pending_review" } },
      );
    });

    it("passes include_completed when completed items are visible", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "item-1" } } },
      });

      const { result } = renderHook(
        () => useNextItem("queue-1", { includeCompleted: true }),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/next-item/",
        { params: { include_completed: true } },
      );
    });

    it("leaves completed queue default navigation to the backend", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "item-1", status: "completed" } } },
      });

      const { result } = renderHook(() => useNextItem("queue-1"), {
        wrapper: createQueryWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/next-item/",
        undefined,
      );
      expect(result.current.data).toEqual({
        id: "item-1",
        status: "completed",
      });
    });

    it("passes review view mode for manager submission browsing", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "item-1" } } },
      });

      const { result } = renderHook(
        () =>
          useNextItem("queue-1", {
            viewMode: "review",
            includeCompleted: true,
          }),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/next-item/",
        { params: { view_mode: "review", include_completed: true } },
      );
    });

    it("passes review status with review view mode for review queues", async () => {
      axios.get.mockResolvedValueOnce({
        data: { result: { item: { id: "item-1" } } },
      });

      const { result } = renderHook(
        () =>
          useNextItem("queue-1", {
            viewMode: "review",
            reviewStatus: "pending_review",
          }),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/next-item/",
        { params: { view_mode: "review", review_status: "pending_review" } },
      );
    });
  });

  describe("useQueueItemsForSource", () => {
    it("passes span_notes_source_id for trace call annotation notes", async () => {
      axios.get.mockResolvedValueOnce({ data: { result: [] } });

      const { result } = renderHook(
        () =>
          useQueueItemsForSource([
            {
              sourceType: "trace",
              sourceId: "trace-1",
              spanNotesSourceId: "span-1",
            },
          ]),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/for-source/",
        {
          params: {
            sources: JSON.stringify([
              {
                source_type: "trace",
                source_id: "trace-1",
                span_notes_source_id: "span-1",
              },
            ]),
          },
        },
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
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: queueItemKeys.all("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotationQueueKeys.progress("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotationQueueKeys.all,
        });
      });
    });
  });

  describe("useAssignQueueItems", () => {
    it("optimistically updates annotate detail assignees before the request returns", async () => {
      let resolveRequest;
      axios.post.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveRequest = resolve;
          }),
      );
      const queryClient = createTestQueryClient();
      queryClient.setQueryData(annotateKeys.detail("queue-1", "item-1"), {
        data: {
          result: {
            item: { id: "item-1", assigned_users: [] },
          },
        },
      });

      const { result } = renderHook(() => useAssignQueueItems(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemIds: ["item-1"],
        userIds: ["user-1"],
        action: "add",
        assignees: [
          {
            user_id: "user-1",
            name: "Kartik",
            email: "kartik.nvj@futureagi.com",
          },
        ],
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/assign/",
          {
            item_ids: ["item-1"],
            user_ids: ["user-1"],
            action: "add",
          },
        );
        expect(
          queryClient.getQueryData(annotateKeys.detail("queue-1", "item-1"))
            .data.result.item.assigned_users,
        ).toEqual([
          {
            id: "user-1",
            name: "Kartik",
            email: "kartik.nvj@futureagi.com",
          },
        ]);
      });

      resolveRequest({ data: { result: {} } });
      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });

    it("keeps existing assignee display data when reassignment only sends user IDs", async () => {
      axios.post.mockResolvedValueOnce({ data: { result: {} } });
      const queryClient = createTestQueryClient();
      queryClient.setQueryData(annotateKeys.detail("queue-1", "item-1"), {
        data: {
          result: {
            item: {
              id: "item-1",
              assigned_users: [
                {
                  id: "user-2",
                  name: "Nikhil",
                  email: "nikhil@example.com",
                },
              ],
            },
          },
        },
      });

      const { result } = renderHook(() => useAssignQueueItems(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemIds: ["item-1"],
        userIds: ["user-2"],
        action: "set",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/assign/",
          {
            item_ids: ["item-1"],
            user_ids: ["user-2"],
            action: "set",
          },
        );
        expect(
          queryClient.getQueryData(annotateKeys.detail("queue-1", "item-1"))
            .data.result.item.assigned_users,
        ).toEqual([
          {
            id: "user-2",
            name: "Nikhil",
            email: "nikhil@example.com",
          },
        ]);
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });
    });

    it("rolls back optimistic assignees when assignment fails", async () => {
      axios.post.mockRejectedValueOnce(new Error("failed"));
      const queryClient = createTestQueryClient();
      queryClient.setQueryData(annotateKeys.detail("queue-1", "item-1"), {
        data: {
          result: {
            item: {
              id: "item-1",
              assigned_users: [{ id: "user-2", name: "Nikhil" }],
            },
          },
        },
      });

      const { result } = renderHook(() => useAssignQueueItems(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemIds: ["item-1"],
        userIds: ["user-1"],
        action: "set",
        assignees: [{ user_id: "user-1", name: "Kartik" }],
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
      expect(
        queryClient.getQueryData(annotateKeys.detail("queue-1", "item-1")).data
          .result.item.assigned_users,
      ).toEqual([{ id: "user-2", name: "Nikhil" }]);
    });

    it("surfaces the backend's specific message as the sole error toast", async () => {
      // Regression: the old onError showed a hardcoded "Failed to assign items"
      // beside the global handler's specific message, so two toasts stacked. The
      // mutation now sets meta.errorHandled (global handler stays out) and its
      // onError surfaces the backend's specific message as the single toast.
      enqueueSnackbar.mockClear();
      axios.post.mockRejectedValueOnce({
        result: "Only queue managers can manage queue item assignments.",
        statusCode: 403,
      });

      const { result } = renderHook(() => useAssignQueueItems(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemIds: ["item-1"],
        userIds: ["user-1"],
        action: "set",
      });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Only queue managers can manage queue item assignments.",
        { variant: "error" },
      );
    });
  });

  describe("useCompleteItem", () => {
    it("sends annotator-mode next-item filter when completing", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { next_item: null } },
      });

      const { result } = renderHook(() => useCompleteItem(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        exclude: ["item-1"],
        excludeReviewStatus: "pending_review",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/complete/",
          {
            exclude: ["item-1"],
            exclude_review_status: "pending_review",
          },
        );
      });
    });

    it("sends include_completed when completing from all-items mode", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { next_item: null } },
      });

      const { result } = renderHook(() => useCompleteItem(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        includeCompleted: true,
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/complete/",
          { include_completed: true },
        );
      });
    });

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
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: queueItemKeys.all("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotationQueueKeys.progress("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotationQueueKeys.all,
        });
      });
    });
  });

  describe("useSkipItem", () => {
    it("sends include_completed when skipping from all-items mode", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { next_item: null } },
      });

      const { result } = renderHook(() => useSkipItem(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        exclude: ["item-1"],
        includeCompleted: true,
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/skip/",
          { exclude: ["item-1"], include_completed: true },
        );
      });
    });
  });

  describe("useReviewItem", () => {
    it("posts overall and label-level reviewer feedback", async () => {
      axios.post.mockResolvedValueOnce({ data: { result: {} } });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useReviewItem(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        action: "request_changes",
        notes: "overall feedback",
        labelComments: [
          {
            label_id: "label-1",
            target_annotator_id: "user-1",
            comment: "fix this label",
          },
        ],
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/review/",
          {
            action: "request_changes",
            notes: "overall feedback",
            label_comments: [
              {
                label_id: "label-1",
                target_annotator_id: "user-1",
                comment: "fix this label",
              },
            ],
          },
        );
      });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith("Changes requested", {
          variant: "warning",
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.detail("queue-1", "item-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.annotations("queue-1", "item-1"),
        });
      });
    });
  });

  describe("useBulkReviewItems", () => {
    it("posts selected item ids and invalidates review-related queries", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { reviewed: 2, errors: [] } },
      });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useBulkReviewItems(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemIds: ["item-1", "item-2"],
        action: "approve",
        notes: "Looks good.",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/bulk-review/",
          {
            item_ids: ["item-1", "item-2"],
            action: "approve",
            notes: "Looks good.",
          },
        );
      });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith("2 items reviewed", {
          variant: "success",
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: queueItemKeys.all("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.nextItem("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotationQueueKeys.progress("queue-1"),
        });
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotationQueueKeys.analytics("queue-1"),
        });
      });
    });

    it("warns when the backend skips some selected items", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { reviewed: 1, errors: [{ item_id: "item-2" }] } },
      });

      const { result } = renderHook(() => useBulkReviewItems(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemIds: ["item-1", "item-2"],
        action: "approve",
      });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "1 items reviewed, 1 skipped",
          { variant: "warning" },
        );
      });
    });
  });

  describe("useOrgMembersInfinite", () => {
    it("refetches on mount so newly added members are available in queue settings", async () => {
      const queryClient = createTestQueryClient();
      queryClient.setQueryData(["org-members-infinite", "org-1", ""], {
        pages: [
          {
            data: {
              results: [
                {
                  id: "old-user",
                  name: "Old cached member",
                  email: "old@example.com",
                },
              ],
              current_page: 1,
              total_pages: 1,
            },
          },
        ],
        pageParams: [1],
      });
      axios.get.mockResolvedValueOnce({
        data: {
          results: [
            {
              id: "new-user",
              name: "New member",
              email: "new@example.com",
            },
          ],
          current_page: 1,
          total_pages: 1,
        },
      });

      const { result } = renderHook(() => useOrgMembersInfinite("org-1"), {
        wrapper: createQueryWrapper(queryClient),
      });

      expect(result.current.data).toEqual([
        {
          id: "old-user",
          name: "Old cached member",
          email: "old@example.com",
        },
      ]);

      await waitFor(() => {
        expect(axios.get).toHaveBeenCalledWith(
          "/model-hub/organizations/org-1/users/",
          { params: { page: 1, limit: 30 } },
        );
      });
      await waitFor(() => {
        expect(result.current.data).toEqual([
          {
            id: "new-user",
            name: "New member",
            email: "new@example.com",
          },
        ]);
      });
    });
  });

  describe("discussion hooks", () => {
    it("fetches discussion comments and threads for live collaboration", async () => {
      axios.get.mockResolvedValueOnce({
        data: {
          result: {
            review_comments: [{ id: "comment-1" }],
            review_threads: [{ id: "thread-1" }],
          },
        },
      });

      const { result } = renderHook(
        () => useItemDiscussion("queue-1", "item-1"),
        {
          wrapper: createQueryWrapper(),
        },
      );

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(axios.get).toHaveBeenCalledWith(
        "/model-hub/annotation-queues/queue-1/items/item-1/discussion/",
      );
      expect(result.current.data).toEqual({
        review_comments: [{ id: "comment-1" }],
        review_threads: [{ id: "thread-1" }],
      });
    });

    it("posts root comments, scoped replies, and mentions", async () => {
      axios.post.mockResolvedValueOnce({ data: { result: {} } });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
      const { result } = renderHook(() => useCreateDiscussionComment(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        threadId: "thread-1",
        labelId: "label-1",
        comment: "@Narda please check",
        mentionedUserIds: ["user-2"],
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/discussion/",
          {
            comment: "@Narda please check",
            thread_id: "thread-1",
            label_id: "label-1",
            mentioned_user_ids: ["user-2"],
          },
        );
      });
      await waitFor(() => {
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.discussion("queue-1", "item-1"),
        });
      });
      expect(invalidateSpy).toHaveBeenCalledTimes(1);
      expect(invalidateSpy).not.toHaveBeenCalledWith({
        queryKey: annotateKeys.detail("queue-1", "item-1"),
      });
      expect(invalidateSpy).not.toHaveBeenCalledWith({
        queryKey: annotateKeys.annotations("queue-1", "item-1"),
      });
    });

    it("posts resolve, reopen, and reaction actions", async () => {
      axios.post.mockResolvedValue({ data: { result: {} } });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result: resolveResult } = renderHook(
        () => useResolveDiscussionThread(),
        { wrapper: createQueryWrapper(queryClient) },
      );
      resolveResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        threadId: "thread-1",
      });

      const { result: reopenResult } = renderHook(
        () => useReopenDiscussionThread(),
        { wrapper: createQueryWrapper(queryClient) },
      );
      reopenResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        threadId: "thread-1",
      });

      const { result: reactionResult } = renderHook(
        () => useToggleDiscussionReaction(),
        { wrapper: createQueryWrapper(queryClient) },
      );
      reactionResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        commentId: "comment-1",
        emoji: "👍",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/discussion/thread-1/resolve/",
          {},
        );
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/discussion/thread-1/reopen/",
          {},
        );
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/discussion/comments/comment-1/reaction/",
          { emoji: "👍" },
        );
      });
      await waitFor(() => {
        expect(invalidateSpy).toHaveBeenCalledTimes(3);
      });
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: annotateKeys.discussion("queue-1", "item-1"),
      });
      expect(invalidateSpy).not.toHaveBeenCalledWith({
        queryKey: annotateKeys.detail("queue-1", "item-1"),
      });
      expect(invalidateSpy).not.toHaveBeenCalledWith({
        queryKey: annotateKeys.annotations("queue-1", "item-1"),
      });
    });

    it("patches and deletes author comments through the discussion comment endpoint", async () => {
      axios.patch.mockResolvedValueOnce({ data: { result: {} } });
      axios.delete.mockResolvedValueOnce({ data: { result: {} } });
      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result: updateResult } = renderHook(
        () => useUpdateDiscussionComment(),
        { wrapper: createQueryWrapper(queryClient) },
      );
      updateResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        commentId: "comment-1",
        comment: "Updated",
        mentionedUserIds: ["user-2"],
      });

      const { result: deleteResult } = renderHook(
        () => useDeleteDiscussionComment(),
        { wrapper: createQueryWrapper(queryClient) },
      );
      deleteResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        commentId: "comment-1",
      });

      await waitFor(() => {
        expect(axios.patch).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/discussion/comments/comment-1/",
          {
            comment: "Updated",
            mentioned_user_ids: ["user-2"],
          },
        );
        expect(axios.delete).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/discussion/comments/comment-1/",
        );
      });

      await waitFor(() => {
        expect(invalidateSpy).toHaveBeenCalledWith({
          queryKey: annotateKeys.discussion("queue-1", "item-1"),
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

  describe("useEvaluateRule", () => {
    it("surfaces flattened 409 duplicate-run errors as warning", async () => {
      axios.post.mockRejectedValueOnce({
        statusCode: 409,
        result: "A run is already in progress for this rule.",
      });

      const { result } = renderHook(() => useEvaluateRule(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({ queueId: "queue-1", ruleId: "rule-1" });

      await waitFor(() => {
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "A run is already in progress for this rule.",
          { variant: "warning" },
        );
      });
    });
  });
});
