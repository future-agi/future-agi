import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { PROJECT_SOURCE } from "src/utils/constants";
import TracesTab from "../TracesTab";

// Only the stat tiles are under test; the grid, drawers and hooks are stubs.
const state = vi.hoisted(() => ({ aggregates: {}, source: "prototype" }));

vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedTraces: () => ({
    data: { traces: [], total: 0, aggregates: state.aggregates },
    isLoading: false,
  }),
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: state.source } }),
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));

const error = { cluster_id: "E-1", project_id: "project-1" };
const measured = (overrides) => ({
  total_traces: 3,
  avg_score: 0.4,
  avg_turns: null,
  p50_latency: 1000,
  p95_latency: 2000,
  ...overrides,
});

describe("TracesTab stat tiles", () => {
  beforeEach(() => {
    state.source = "prototype";
  });

  it("shows no data for an unmeasured score and hides turns on a text project", () => {
    state.aggregates = measured({ avg_score: null, avg_turns: null });

    render(<TracesTab error={error} />);

    expect(screen.getByText("Avg score").previousSibling).toHaveTextContent(
      "—",
    );
    expect(screen.queryByText("Avg turns")).not.toBeInTheDocument();
  });

  it("shows turns for a voice project that recorded them", () => {
    state.source = PROJECT_SOURCE.SIMULATOR;
    state.aggregates = measured({ avg_turns: 3.5 });

    render(<TracesTab error={error} />);

    expect(screen.getByText("Avg score").previousSibling).toHaveTextContent(
      "0.40",
    );
    expect(screen.getByText("Avg turns").previousSibling).toHaveTextContent(
      "3.5",
    );
  });
});
