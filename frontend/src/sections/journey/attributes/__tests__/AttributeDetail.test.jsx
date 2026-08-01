import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";

import AttributeDetail from "../AttributeDetail";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    project: {
      spanAttributeDetail: () => "/api/traces/span-attribute-detail/",
    },
  },
}));

vi.mock("../AttributeValueChart", () => ({
  default: () => <div>Attribute value chart</div>,
}));

const renderDetail = ({ attributeType } = {}) => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AttributeDetail
        projectId="project-1"
        attributeKey="final_status"
        attributeType={attributeType}
      />
    </QueryClientProvider>,
  );
};

describe("AttributeDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a safe incomplete state without inventing a zero count", async () => {
    axios.get.mockResolvedValue({
      data: {
        key: "final_status",
        query_complete: false,
        query_status: "degraded",
        query_error_code: "read_budget_exceeded",
        query_window_start: "2026-07-23T12:34:56Z",
        query_window_end: "2026-07-30T12:34:56Z",
      },
    });

    renderDetail();

    const warning =
      "Attribute statistics are temporarily incomplete. No zero counts were inferred; try again shortly.";
    await waitFor(() => expect(screen.getByText(warning)).toBeInTheDocument());

    expect(screen.getAllByText(warning)).toHaveLength(1);
    expect(screen.getByText("final_status")).toBeInTheDocument();
    expect(screen.queryByText("0 spans")).not.toBeInTheDocument();
    expect(screen.queryByText("undefined spans")).not.toBeInTheDocument();
  });

  it("passes a picker-provided type to the detail endpoint", async () => {
    axios.get.mockResolvedValue({
      data: {
        key: "final_status",
        type: "string",
        count: 1,
        query_complete: true,
        query_status: "complete",
      },
    });

    renderDetail({ attributeType: "string" });

    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith(
        "/api/traces/span-attribute-detail/",
        {
          params: {
            project_id: "project-1",
            key: "final_status",
            type: "string",
          },
        },
      ),
    );
  });
});
