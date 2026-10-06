import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  useBulkCreateScores,
  useScoresForSource,
  useSpanNotes,
} from "../scores";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
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
            scores: [
              {
                label_id: "label-1",
                value: { value: "up" },
                score_source: "human",
              },
            ],
            notes: "",
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

    it("pins the write to the project the drawer showed", async () => {
      axios.post.mockResolvedValueOnce({
        data: { result: { scores: [], errors: [] } },
      });

      const { result } = renderHook(() => useBulkCreateScores(), {
        wrapper: createQueryWrapper(),
      });

      result.current.mutate({
        sourceType: "observation_span",
        sourceId: "span-1",
        scores: [{ label_id: "label-1", value: { value: "up" } }],
        projectId: "project-1",
      });

      await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
      expect(axios.post.mock.calls[0][1]).toEqual(
        expect.objectContaining({
          source_type: "observation_span",
          source_id: "span-1",
          project_id: "project-1",
        }),
      );
    });
  });

  describe("scores for-source reads", () => {
    const readParams = () =>
      axios.get.mock.calls.map(([url]) => {
        const parsed = new URL(url, "http://localhost");
        expect(parsed.pathname).toBe("/model-hub/scores/for-source/");
        return Object.fromEntries(parsed.searchParams);
      });

    beforeEach(() => {
      axios.get.mockResolvedValue({
        data: { status: true, result: [], span_notes: [] },
      });
    });

    it("pins the read to the project the drawer showed", async () => {
      renderHook(
        () =>
          useScoresForSource("observation_span", "span-1", {
            projectId: "project-1",
          }),
        { wrapper: createQueryWrapper() },
      );

      await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
      expect(readParams()).toEqual([
        {
          source_type: "observation_span",
          source_id: "span-1",
          project_id: "project-1",
        },
      ]);
    });

    it("reads each project's copy of one span on its own", async () => {
      renderHook(
        () => [
          useScoresForSource("trace", "trace-1", { projectId: "project-1" }),
          useScoresForSource("trace", "trace-1", { projectId: "project-2" }),
        ],
        { wrapper: createQueryWrapper() },
      );

      await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
      expect(
        readParams()
          .map((params) => params.project_id)
          .sort(),
      ).toEqual(["project-1", "project-2"]);
    });

    it("leaves a read without a project unpinned", async () => {
      renderHook(() => useScoresForSource("trace", "trace-1"), {
        wrapper: createQueryWrapper(),
      });

      await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
      expect(readParams()).toEqual([
        { source_type: "trace", source_id: "trace-1" },
      ]);
    });

    it("pins the span notes read to the drawer's project", async () => {
      renderHook(() => useSpanNotes("span-1", { projectId: "project-1" }), {
        wrapper: createQueryWrapper(),
      });

      await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(1));
      expect(readParams()).toEqual([
        {
          source_type: "observation_span",
          source_id: "span-1",
          project_id: "project-1",
        },
      ]);
    });
  });
});
