import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { useBulkCreateScores } from "../scores";

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

describe("Scores API", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("useBulkCreateScores", () => {
    it("sends trace scores with a separate span notes source", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { scores: [], errors: [] } },
      });

      const { result } = renderHook(() => useBulkCreateScores(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        sourceType: "trace",
        sourceId: "trace-1",
        scores: [{ label_id: "label-1", value: { value: "up" } }],
        spanNotes: "whole item note",
        includeSpanNotes: true,
        spanNotesSourceId: "span-1",
      });

      await waitFor(() => {
        expect(axios.post).toHaveBeenCalledWith(
          "/model-hub/scores/bulk/",
          {
            source_type: "trace",
            source_id: "trace-1",
            scores: [{ label_id: "label-1", value: { value: "up" } }],
            notes: "",
            score_source: "human",
            span_notes: "whole item note",
            span_notes_source_id: "span-1",
          },
          {
            headers: {
              "Content-Type": "application/json",
            },
          },
        );
      });
    });
  });
});
