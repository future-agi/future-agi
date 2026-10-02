import React from "react";
import PropTypes from "prop-types";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    project: { spanAttributeDetail: () => "/span-attribute-detail/" },
  },
}));
vi.mock("../AttributeValueChart", () => ({ default: () => <div>chart</div> }));

import AttributeDetail from "../AttributeDetail";

function Wrapper({ children }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
Wrapper.propTypes = { children: PropTypes.node };

describe("AttributeDetail", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows only an exact preparing state while a cold refresh runs", async () => {
    mocks.get.mockResolvedValue({
      data: {
        key: "final_status",
        type: null,
        count: 0,
        unique_values: 0,
        top_values: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: true,
        query_refresh_failed: false,
      },
    });

    render(
      <AttributeDetail projectId="project-large" attributeKey="final_status" />,
      { wrapper: Wrapper },
    );

    expect(await screen.findByText("Loading attribute details…")).toBeVisible();
    expect(
      screen.queryByText(/incomplete|sample-limited/i),
    ).not.toBeInTheDocument();
  });

  it("terminates a failed cold refresh with an explicit retry", async () => {
    mocks.get.mockResolvedValue({
      data: {
        key: "final_status",
        type: null,
        count: 0,
        unique_values: 0,
        top_values: [],
        query_complete: false,
        query_status: "pending",
        query_sampled: false,
        query_refreshing: false,
        query_refresh_failed: true,
      },
    });

    render(
      <AttributeDetail projectId="project-large" attributeKey="final_status" />,
      { wrapper: Wrapper },
    );

    expect(
      await screen.findByText(
        "Exact attribute details could not be prepared. Retry when you are ready.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
    expect(
      screen.queryByText("Loading attribute details…"),
    ).not.toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 1100));
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("keeps the completed exact snapshot visible while requesting a refresh", async () => {
    const complete = {
      data: {
        key: "final_status",
        type: "string",
        count: 3,
        unique_values: 2,
        top_values: [
          { value: "Rejected", count: 2, percentage: 66.666 },
          { value: "Accepted", count: 1, percentage: 33.333 },
        ],
        query_complete: true,
        query_status: "complete",
        query_sampled: false,
        query_refreshing: false,
        query_refresh_failed: false,
      },
    };
    mocks.get.mockResolvedValue(complete);

    render(
      <AttributeDetail projectId="project-large" attributeKey="final_status" />,
      { wrapper: Wrapper },
    );

    expect(await screen.findByText("Rejected")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(mocks.get).toHaveBeenNthCalledWith(2, "/span-attribute-detail/", {
      params: {
        project_id: "project-large",
        key: "final_status",
        refresh: true,
      },
    });
    expect(screen.getByText("Rejected")).toBeVisible();
  });

  it("shows a sanitized retry state when the exact snapshot request fails", async () => {
    mocks.get.mockRejectedValue(new Error("secret database error"));

    render(
      <AttributeDetail projectId="project-large" attributeKey="final_status" />,
      { wrapper: Wrapper },
    );

    expect(
      await screen.findByText("Attribute details could not be loaded."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
    expect(screen.queryByText(/secret|database/i)).not.toBeInTheDocument();
  });
});
