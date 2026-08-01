import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useErrorFeedApiParams, useErrorFeedStore } from "../store";

const DEFAULT_LIST_STATE = {
  searchQuery: "",
  selectedProject: "",
  selectedEnvironment: "",
  selectedStatus: "",
  selectedSeverity: "",
  selectedErrorType: "",
  selectedFixLayer: "",
  selectedSource: "",
  timeRange: "7",
  sortBy: "lastSeen",
  sortDir: "desc",
  page: 0,
  pageSize: 25,
};

describe("Error Feed store", () => {
  afterEach(() => {
    act(() => {
      useErrorFeedStore.setState(DEFAULT_LIST_STATE);
    });
  });

  it("maps severity filter and severity sort to backend query params", () => {
    const { result } = renderHook(() => useErrorFeedApiParams());

    act(() => {
      useErrorFeedStore.getState().setSelectedSeverity("medium");
    });

    expect(result.current).toMatchObject({
      severity: "medium",
      sort_by: "last_seen",
    });

    act(() => {
      useErrorFeedStore.getState().setSortBy("severity");
    });

    expect(result.current).toMatchObject({
      severity: "medium",
      sort_by: "severity",
      sort_dir: "desc",
    });
  });
});
