import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import TraceTreeV2 from "../TraceTreeV2";

// jsdom does not implement scrollIntoView.
const originalScroll = Object.getOwnPropertyDescriptor(
  Element.prototype,
  "scrollIntoView",
);
beforeAll(() =>
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  }),
);
afterAll(() => {
  if (originalScroll) {
    Object.defineProperty(Element.prototype, "scrollIntoView", originalScroll);
  } else {
    delete Element.prototype.scrollIntoView;
  }
});

const spans = [
  {
    observation_span: {
      id: "span-root",
      name: "agent_run",
      observation_type: "agent",
      status: "OK",
      latency_ms: 1400,
    },
    children: [
      {
        observation_span: {
          id: "span-origin",
          name: "tool_lookup",
          observation_type: "tool",
          status: "OK",
          latency_ms: 420,
          span_attributes: {
            "error_feed.step": 1,
            "error_feed.role": "origin_span",
            "error_feed.alias": "S44",
            "error_feed.headline": "Tool response truncated after 200 chars",
          },
        },
        children: [],
      },
      {
        observation_span: {
          id: "span-failure",
          name: "final_reply",
          observation_type: "llm",
          status: "ERROR",
          latency_ms: 610,
          span_attributes: {
            "error_feed.step": 2,
            "error_feed.role": "symptom_span",
            "error_feed.alias": "S96",
            "error_feed.headline": "Assistant invented missing balance figure",
          },
        },
        children: [],
      },
    ],
  },
];

const traceIssues = [
  {
    cluster_id: "cluster-a",
    title: "Truncated tool output causes hallucinated balance",
    origin_alias: "S44",
    symptom_alias: "S96",
  },
  {
    cluster_id: "cluster-b",
    title: "Missing retry after rate-limit warning",
    origin_alias: "S12",
    symptom_alias: "S18",
  },
];

describe("TraceTreeV2 All vs Key path ordering and defaults", () => {
  it("puts All first and selects All by default for Tracing and linked traces", () => {
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="span-root"
        onSelectSpan={vi.fn()}
        traceIssues={traceIssues}
        activeClusterId="cluster-a"
      />,
    );

    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "All (3)",
      "Key path (2)",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    expect(screen.getByText("agent_run")).toBeInTheDocument();
  });

  it("switches cleanly from All to Key path and selects clicked step span", () => {
    const onSelectSpan = vi.fn();
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="span-root"
        onSelectSpan={onSelectSpan}
        defaultCompressed={false}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Key path (2)" }));
    // Switching from an unlisted root span into Key path immediately selects the origin step.
    expect(onSelectSpan).toHaveBeenCalledWith("span-origin");
    expect(
      screen.getByText("Tool response truncated after 200 chars"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Assistant invented missing balance figure"),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByText("Assistant invented missing balance figure"),
    );
    expect(onSelectSpan).toHaveBeenLastCalledWith("span-failure");
  });

  it("does not deselect the active key-path step when clicked again", () => {
    const onSelectSpan = vi.fn();
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="span-origin"
        onSelectSpan={onSelectSpan}
        defaultCompressed
      />,
    );

    fireEvent.click(screen.getByText("Tool response truncated after 200 chars"));
    expect(onSelectSpan).not.toHaveBeenCalled();
  });

  it("opens on Key path only when defaultCompressed is explicitly true", () => {
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="span-origin"
        onSelectSpan={vi.fn()}
        defaultCompressed
      />,
    );

    const tabs = screen.getAllByRole("tab");
    expect(tabs[0]).toHaveTextContent("All (3)");
    expect(tabs[1]).toHaveTextContent("Key path (2)");
    expect(tabs[0]).toHaveAttribute("aria-selected", "false");
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
  });

  it("switches issues from the flat issue menu without overriding All mode", () => {
    const onSelectIssue = vi.fn();
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="span-root"
        onSelectSpan={vi.fn()}
        traceIssues={traceIssues}
        activeClusterId="cluster-a"
        onSelectIssue={onSelectIssue}
      />,
    );

    fireEvent.click(
      screen.getByText("Truncated tool output causes hallucinated balance"),
    );
    fireEvent.click(
      screen.getByText(/Missing retry after rate-limit warning/),
    );
    expect(onSelectIssue).toHaveBeenCalledWith("cluster-b");
    expect(screen.getByRole("tab", { name: "All (3)" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
