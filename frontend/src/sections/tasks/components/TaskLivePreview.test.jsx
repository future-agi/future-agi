import React from "react";
import PropTypes from "prop-types";
import { useForm } from "react-hook-form";
import { render, screen, waitFor } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TaskLivePreview from "./TaskLivePreview";

const useQueryMock = vi.hoisted(() => vi.fn());

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual("@tanstack/react-query");
  return { ...actual, useQuery: useQueryMock };
});

vi.mock("src/sections/evals/utils/evalExecution", () => ({
  executeEvalForRow: vi.fn(),
}));

vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => null,
}));

vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => null,
}));

vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => null,
}));

const Harness = ({ onTestStateChange }) => {
  const { control } = useForm({
    defaultValues: {
      filters: [],
      startDate: "2026-07-01",
      endDate: "2026-07-30",
      rowType: "spans",
      evalsDetails: [{ template_id: "eval-1" }],
    },
  });
  return (
    <TaskLivePreview
      control={control}
      projectId="project-1"
      onTestStateChange={onTestStateChange}
    />
  );
};

Harness.propTypes = {
  onTestStateChange: PropTypes.func.isRequired,
};

describe("TaskLivePreview degraded query contract", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
    useQueryMock.mockImplementation(({ queryKey }) => {
      if (queryKey[0] === "task-preview-list") {
        return {
          data: {
            rows: [],
            total: 0,
            columns: [],
            isDegraded: true,
          },
          isLoading: false,
          isFetching: false,
          isError: false,
        };
      }
      return { data: null, isLoading: false };
    });
  });

  it("shows narrowing guidance and disables test/create readiness", async () => {
    const onTestStateChange = vi.fn();

    render(<Harness onTestStateChange={onTestStateChange} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Preview reached the query safety limit",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Narrow the date range or filters, then retry",
    );
    await waitFor(() => {
      expect(onTestStateChange).toHaveBeenLastCalledWith({
        canTest: false,
        isTesting: false,
        isPreviewDegraded: true,
      });
    });
  });
});
