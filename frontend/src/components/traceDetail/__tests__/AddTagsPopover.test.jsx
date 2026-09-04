import React from "react";
import { render } from "src/utils/test-utils";
import { describe, expect, it, vi } from "vitest";
import AddTagsPopover from "../AddTagsPopover";
import { hashColor } from "../tagUtils";

const captured = vi.hoisted(() => ({
  filterArgs: null,
  inputProps: null,
}));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardFilterValues: (args) => {
    captured.filterArgs = args;
    return { data: ["Production", "Existing"] };
  },
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("src/utils/axios", () => ({
  default: { patch: vi.fn(), post: vi.fn() },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("../TagChip", () => ({
  default: () => null,
}));

vi.mock("../TagInput", () => {
  const MockTagInput = (props) => {
    captured.inputProps = props;
    return null;
  };
  MockTagInput.displayName = "TagInputMock";
  return { default: MockTagInput };
});

describe("AddTagsPopover project tag suggestions", () => {
  it("loads project tags and passes normalized suggestions to TagInput", () => {
    render(
      <AddTagsPopover
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        projectId="project-1"
        traceId="trace-1"
        currentTags={["Existing"]}
      />,
    );

    expect(captured.filterArgs).toEqual({
      metricName: "tag",
      metricType: "system_metric",
      projectIds: ["project-1"],
      source: "traces",
      enabled: true,
    });
    expect(captured.inputProps.existingNames).toEqual(["Existing"]);
    expect(captured.inputProps.suggestions).toEqual([
      { name: "Production", color: hashColor("Production") },
      { name: "Existing", color: hashColor("Existing") },
    ]);
  });
});
