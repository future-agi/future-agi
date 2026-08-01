import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import axios from "src/utils/axios";

import AttributesView from "../AttributesView";

vi.mock("react-router-dom", () => ({
  useParams: () => ({ id: "project-1" }),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    project: {
      spanAttributeKeys: () => "/api/traces/span-attribute-keys/",
    },
  },
}));

vi.mock("../AttributeDetail", () => ({
  default: ({ attributeKey, attributeType }) => (
    <div>
      Selected detail: {attributeKey || "none"} ({attributeType || "untyped"})
    </div>
  ),
}));

const renderView = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AttributesView />
    </QueryClientProvider>,
  );
};

describe("AttributesView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("marks unknown counts and permits manual keys when discovery degrades", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: [{ key: "final_status", type: "string" }],
        query_complete: false,
        query_status: "degraded",
        query_error_code: "read_budget_exceeded",
      },
    });

    renderView();

    expect(
      await screen.findByText(
        "Attribute discovery is incomplete. Type an attribute key to continue.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Count unavailable")).toBeInTheDocument();
    expect(screen.queryByText("0 spans")).not.toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText("Search attributes..."),
      "custom.status",
    );
    await userEvent.click(screen.getByText('Use "custom.status"'));

    await waitFor(() =>
      expect(
        screen.getByText("Selected detail: custom.status (untyped)"),
      ).toBeInTheDocument(),
    );
  });

  it("retains the selected picker type for the detail request", async () => {
    axios.get.mockResolvedValue({
      data: {
        result: [{ key: "final_status", type: "string" }],
        query_complete: true,
        query_status: "complete",
      },
    });

    renderView();

    await userEvent.click(await screen.findByText("final_status"));

    expect(
      screen.getByText("Selected detail: final_status (string)"),
    ).toBeInTheDocument();
  });
});
