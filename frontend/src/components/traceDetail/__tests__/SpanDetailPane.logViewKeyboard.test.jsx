import { describe, it, expect } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "src/utils/test-utils";
import SpanDetailPane from "../SpanDetailPane";

const START = "2026-03-29T10:00:00.000Z";
const root = {
  id: "root",
  trace: "trace-1",
  name: "invoke_agent",
  observation_type: "agent",
  start_time: START,
  latency_ms: 1000,
};
// No input, output or attributes: an opened row reads "No data available".
const allSpans = [
  {
    observation_span: root,
    children: [
      {
        observation_span: {
          id: "tool",
          name: "search tool",
          observation_type: "tool",
          start_time: START,
          latency_ms: 200,
        },
        children: [],
      },
    ],
  },
];

// SW5-32: a Log View row opens from the keyboard as well as the mouse.
describe("SpanDetailPane Log View rows", () => {
  it("are focusable disclosure buttons that Enter and Space open and close", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <SpanDetailPane
          entry={{ observation_span: root }}
          allSpans={allSpans}
          traceStartTime={START}
          projectId="project-1"
          onClose={() => {}}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText("Log View"));

    const row = screen.getByText("search tool").closest('[role="button"]');
    expect(row).not.toBeNull();
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("No data available")).not.toBeInTheDocument();

    fireEvent.keyDown(row, { key: "Enter" });
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("No data available")).toBeInTheDocument();

    fireEvent.keyDown(row, { key: " " });
    expect(row).toHaveAttribute("aria-expanded", "false");
  });
});
