/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SharedView from "../SharedView";

const mocks = vi.hoisted(() => ({
  useResolveSharedLink: vi.fn(),
}));

vi.mock("react-router", async () => {
  const actual = await vi.importActual("react-router");
  return {
    ...actual,
    useParams: () => ({ token: "share-token" }),
  };
});

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }) => <>{children}</>,
}));

vi.mock("src/api/shared-links", () => ({
  useResolveSharedLink: mocks.useResolveSharedLink,
}));

vi.mock("src/components/traceDetail/TraceTreeV2", () => ({
  default: () => <div>trace tree</div>,
}));

vi.mock("src/components/traceDetail/SpanDetailPane", () => ({
  default: () => <div>span detail</div>,
}));

describe("SharedView dashboard rendering", () => {
  beforeEach(() => {
    mocks.useResolveSharedLink.mockReset();
  });

  it("renders a resolved dashboard share as a read-only dashboard summary", () => {
    mocks.useResolveSharedLink.mockReturnValue({
      data: {
        resource_type: "dashboard",
        resource_id: "dashboard-1234567890",
        data: {
          id: "dashboard-1234567890",
          name: "Production latency",
          description: "Live latency dashboard",
          widget_count: 1,
          widgets: [
            {
              id: "widget-1",
              name: "Latency p95",
              description: "P95 response time",
              width: 6,
              height: 4,
              query_config: {
                time_range: { preset: "7D" },
                metrics: [
                  {
                    name: "latency",
                    display_name: "Latency",
                    aggregation: "p95",
                    type: "system_metric",
                  },
                ],
              },
              chart_config: { chart_type: "line" },
            },
          ],
        },
      },
      isLoading: false,
      isError: false,
      error: null,
    });

    render(<SharedView />);

    expect(screen.getByText("Shared dashboard")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Production latency" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Live latency dashboard")).toBeInTheDocument();
    expect(screen.getByText("1 widget")).toBeInTheDocument();
    expect(screen.getByText("Latency p95")).toBeInTheDocument();
    expect(screen.getByText("P95 response time")).toBeInTheDocument();
    expect(screen.getByText("line")).toBeInTheDocument();
    expect(screen.getByText("7D")).toBeInTheDocument();
    expect(screen.getByText("p95 Latency")).toBeInTheDocument();
  });

  it("renders a resolved project share with an open project action", () => {
    mocks.useResolveSharedLink.mockReturnValue({
      data: {
        resource_type: "project",
        resource_id: "project-1234567890",
        data: {
          id: "project-1234567890",
          name: "Production observe",
          trace_type: "observe",
          model_type: "GenerativeLLM",
          workspace: "workspace-123",
          created_at: "2026-05-30T10:00:00Z",
          updated_at: "2026-05-30T11:00:00Z",
          url_path: "/dashboard/observe/project-1234567890/llm-tracing",
        },
      },
      isLoading: false,
      isError: false,
      error: null,
    });

    render(<SharedView />);

    expect(screen.getByText("Shared project")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Production observe" }),
    ).toBeInTheDocument();
    expect(screen.getByText("observe")).toBeInTheDocument();
    expect(screen.getByText("GenerativeLLM")).toBeInTheDocument();
    expect(screen.getByText("Project details")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open project/i })).toHaveAttribute(
      "href",
      "/dashboard/observe/project-1234567890/llm-tracing",
    );
  });

  it("keeps unsupported shared resource types bounded instead of crashing", () => {
    mocks.useResolveSharedLink.mockReturnValue({
      data: {
        resource_type: "eval_run",
        resource_id: "eval-run-123",
        data: { id: "eval-run-123", name: "Unsupported eval" },
      },
      isLoading: false,
      isError: false,
      error: null,
    });

    render(<SharedView />);

    expect(screen.getByText("Shared eval_run")).toBeInTheDocument();
    expect(screen.getByText("Viewing shared eval_run")).toBeInTheDocument();
    expect(screen.getByText(/Unsupported eval/)).toBeInTheDocument();
  });
});
