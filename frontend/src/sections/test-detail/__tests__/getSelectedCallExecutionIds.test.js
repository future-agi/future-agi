import { describe, it, expect, beforeEach } from "vitest";
import {
  getSelectedCallExecutionIds,
  getSelectedCallExecutionIdsFilter,
} from "../common";
import { useTestDetailStore } from "../states";

const resetStore = () => {
  useTestDetailStore.setState({
    selectedFixableRecommendations: [],
    selectedNonFixableRecommendations: [],
  });
};

describe("getSelectedCallExecutionIds (TH-4954 regression)", () => {
  beforeEach(resetStore);

  it("returns [] when nothing is selected", () => {
    expect(getSelectedCallExecutionIds()).toEqual([]);
  });

  it("returns IDs from selectedFixableRecommendations using the camelCase key the store writes", () => {
    // Mirrors what toggleSelectedFixableRecommendation pushes: { index, callExecutionIds }
    useTestDetailStore.setState({
      selectedFixableRecommendations: [
        { index: 0, callExecutionIds: ["a", "b"] },
        { index: 1, callExecutionIds: ["c"] },
      ],
    });

    expect(getSelectedCallExecutionIds().sort()).toEqual(["a", "b", "c"]);
  });

  it("returns IDs from selectedNonFixableRecommendations", () => {
    useTestDetailStore.setState({
      selectedNonFixableRecommendations: [
        { index: 0, callExecutionIds: ["x", "y"] },
      ],
    });

    expect(getSelectedCallExecutionIds().sort()).toEqual(["x", "y"]);
  });

  it("dedupes IDs across fixable and non-fixable selections", () => {
    useTestDetailStore.setState({
      selectedFixableRecommendations: [
        { index: 0, callExecutionIds: ["a", "b"] },
      ],
      selectedNonFixableRecommendations: [
        { index: 0, callExecutionIds: ["b", "c"] },
      ],
    });

    expect(getSelectedCallExecutionIds().sort()).toEqual(["a", "b", "c"]);
  });

  it("getSelectedCallExecutionIdsFilter wraps the IDs into a list filter", () => {
    useTestDetailStore.setState({
      selectedFixableRecommendations: [
        { index: 0, callExecutionIds: ["a", "b"] },
      ],
    });

    const filter = getSelectedCallExecutionIdsFilter();
    expect(filter).toHaveLength(1);
    expect(filter[0].column_id).toBe("call_execution_id");
    expect(filter[0].filter_config.filter_op).toBe("in");
    expect(filter[0].filter_config.filter_type).toBe("list");
    expect(filter[0].filter_config.filter_value.sort()).toEqual(["a", "b"]);
  });
});
