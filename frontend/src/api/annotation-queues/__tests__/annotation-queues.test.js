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
  useCreateDiscussionComment,
  useAnnotateDetail,
  useAssignQueueItems,
  useCompleteItem,
  useItemDiscussion,
  useNextItem,
  useOrgMembersInfinite,
  useQueueItemsForSource,
  useSkipItem,
  useReopenDiscussionThread,
  useReviewItem,
  useResolveDiscussionThread,
  useSubmitAnnotations,
  useToggleDiscussionReaction,
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
            user_id: "user-1",
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
        exclude: "item-1",
        excludeReviewStatus: "pending_review",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/complete/",
          {
            exclude: "item-1",
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
        exclude: "item-1",
        includeCompleted: true,
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/annotation-queues/queue-1/items/item-1/skip/",
          { exclude: "item-1", include_completed: true },
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
    });

    it("posts resolve, reopen, and reaction actions", async () => {
      axios.post.mockResolvedValue({ data: { result: {} } });

      const { result: resolveResult } = renderHook(
        () => useResolveDiscussionThread(),
        { wrapper: createQueryWrapper() },
      );
      resolveResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        threadId: "thread-1",
      });

      const { result: reopenResult } = renderHook(
        () => useReopenDiscussionThread(),
        { wrapper: createQueryWrapper() },
      );
      reopenResult.current.mutate({
        queueId: "queue-1",
        itemId: "item-1",
        threadId: "thread-1",
      });

      const { result: reactionResult } = renderHook(
        () => useToggleDiscussionReaction(),
        { wrapper: createQueryWrapper() },
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
