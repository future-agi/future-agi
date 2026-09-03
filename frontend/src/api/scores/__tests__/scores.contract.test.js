import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  ModelHubScoresBulkCreateBody,
  ModelHubScoresBulkCreateResponse,
  ModelHubAnnotationQueuesItemsAnnotationsListResponse,
  ModelHubScoresForSourceResponse,
  ModelHubAnnotationQueuesForSourceResponse,
} from "src/generated/api-contracts/api.zod";
import { useBulkCreateScores, SCORE_ITEM_CONSUMED_FIELDS } from "../scores";

// Keys returned by `result[]` of a list/array response schema.
const arrayItemKeys = (responseSchema) =>
  Object.keys(responseSchema.shape.result.element.shape);

vi.mock("src/utils/axios", () => ({
  default: {
    post: vi.fn(),
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

function createQueryWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

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

describe("Scores API contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sends a contract-valid payload for the real UI shape (no score_source) and defaults to human", async () => {
    axios.post.mockResolvedValueOnce({
      data: { result: { scores: [], errors: [] } },
    });

    const { result } = renderHook(() => useBulkCreateScores(), {
      wrapper: createQueryWrapper(),
    });

    // Mirrors what InlineAnnotator / AnnotationSidebarContent actually send:
    // items carry only label_id/value(/notes); neither a per-item score_source
    // nor a batch scoreSource is provided, so every UI annotation is "human".
    result.current.mutate({
      sourceType: "trace",
      sourceId: "trace-1",
      queueItemId: "11111111-1111-1111-1111-111111111111",
      scores: [
        {
          label_id: "22222222-2222-2222-2222-222222222222",
          value: { value: "up" },
          notes: "",
        },
      ],
    });

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    const sentBody = axios.post.mock.calls[0][1];

    expect(ModelHubScoresBulkCreateBody.safeParse(sentBody).success).toBe(true);
    expect(sentBody.scores[0].score_source).toBe("human");
  });

  // Guards the hook's fallback contract, NOT a current UI path: no UI caller
  // sends per-item provenance today (imported/api/auto scores originate from
  // backend import/ingestion). This pins the `s.score_source ?? scoreSource ??
  // "human"` order so a future non-UI caller's provenance isn't overwritten.
  it("preserves a per-item score_source if a caller ever provides one", async () => {
    axios.post.mockResolvedValueOnce({
      data: { result: { scores: [], errors: [] } },
    });

    const { result } = renderHook(() => useBulkCreateScores(), {
      wrapper: createQueryWrapper(),
    });

    result.current.mutate({
      sourceType: "trace",
      sourceId: "trace-1",
      scores: [
        {
          label_id: "22222222-2222-2222-2222-222222222222",
          value: { value: "up" },
          score_source: "imported",
        },
      ],
      scoreSource: "human",
    });

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    const sentBody = axios.post.mock.calls[0][1];
    expect(sentBody.scores[0].score_source).toBe("imported");
  });

  it("rejects a payload that drifts from the contract (camelCase / renamed keys)", () => {
    const driftedBody = {
      sourceType: "trace",
      sourceId: "trace-1",
      scores: [
        {
          labelId: "22222222-2222-2222-2222-222222222222",
          value: { value: "up" },
          scoreSource: "human",
        },
      ],
    };

    expect(ModelHubScoresBulkCreateBody.safeParse(driftedBody).success).toBe(
      false,
    );
  });

  it("accepts a bulk-create response shaped like the contract", () => {
    const response = {
      status: true,
      result: {
        scores: [
          {
            id: "33333333-3333-3333-3333-333333333333",
            source_type: "trace",
            source_id: "trace-1",
            label_id: "22222222-2222-2222-2222-222222222222",
            value: { value: "up" },
            score_source: "human",
          },
        ],
        errors: [],
      },
    };

    expect(ModelHubScoresBulkCreateResponse.safeParse(response).success).toBe(
      true,
    );
  });

  it("validates the annotation-queue item response the sidebar reads", () => {
    // AnnotationSidebarContent destructures these snake_case keys off each item.
    const response = {
      status: true,
      result: [
        {
          id: "44444444-4444-4444-4444-444444444444",
          source_type: "trace",
          source_id: "trace-1",
          label_id: "22222222-2222-2222-2222-222222222222",
          value: { value: "up" },
          score_source: "human",
        },
      ],
    };

    const parsed =
      ModelHubAnnotationQueuesItemsAnnotationsListResponse.safeParse(response);

    expect(parsed.success).toBe(true);
    expect(Object.keys(parsed.data.result[0])).toContain("score_source");
  });

  // These pin the exact keys each component destructures off the backend
  // response against the generated contract. If a serializer rename drops or
  // renames one of these fields, the contract regenerates and the matching
  // assertion fails — pointing straight at the component that reads it —
  // instead of the page silently rendering blank.
  describe("consumer field reads", () => {
    // src/components/ScoresListSection/ScoresListSection.jsx:108-120
    const SCORES_LIST_CONSUMED = [
      "id",
      "label_id",
      "source_type",
      "source_id",
      "label_name",
      "label_type",
      "value",
      "annotator_name",
      "annotator_email",
      "score_source",
      "notes",
      "updated_at",
      "queue_id",
      "queue_item",
    ];

    const DRAWER_BOTTOM_CONSUMED = [
      "id",
      "label_id",
      "label_name",
      "label_type",
      "label_settings",
      "value",
      "score_source",
      "notes",
      "annotator_name",
      "annotator_email",
      "updated_at",
    ];

    const HOOK_CONSUMED = [
      ...new Set([...SCORES_LIST_CONSUMED, ...DRAWER_BOTTOM_CONSUMED]),
    ];

    it("ScoresListSection reads only fields present in ScoresForSource", () => {
      const available = arrayItemKeys(ModelHubScoresForSourceResponse);
      expect(available).toEqual(expect.arrayContaining(SCORES_LIST_CONSUMED));
    });

    it("drawer-bottom reads only fields present in ScoresForSource", () => {
      const available = arrayItemKeys(ModelHubScoresForSourceResponse);
      expect(available).toEqual(expect.arrayContaining(DRAWER_BOTTOM_CONSUMED));
    });

    it("runtime SCORE_ITEM_CONSUMED_FIELDS stays within the contract and the consumed reads", () => {
      const available = arrayItemKeys(ModelHubScoresForSourceResponse);
      expect(available).toEqual(
        expect.arrayContaining([...SCORE_ITEM_CONSUMED_FIELDS]),
      );
      expect(HOOK_CONSUMED).toEqual(
        expect.arrayContaining([...SCORE_ITEM_CONSUMED_FIELDS]),
      );
    });

    it("AnnotationSidebarContent reads only fields present in QueuesForSource", () => {
      // src/components/traceDetailDrawer/AnnotationSidebarContent.jsx:387-392
      const consumed = [
        "queue",
        "item",
        "labels",
        "existing_scores",
        "existing_notes",
        "existing_label_notes",
      ];
      const available = arrayItemKeys(ModelHubAnnotationQueuesForSourceResponse);
      expect(available).toEqual(expect.arrayContaining(consumed));
    });
  });
});
