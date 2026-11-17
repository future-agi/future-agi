// Additive, inert-when-absent props on Observe's trace
// components (TraceTreeV2 `annotations` / `highlightSpanIds`,
// SpanTreeTimeline `axisRangeMs`). Without them the DOM is unchanged.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import TraceTreeV2 from "../TraceTreeV2";
import SpanTreeTimeline from "../SpanTreeTimeline";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// The annotation fixture owns this icon, not the generic trace tree.
registerOfflineIcon("mdi:triangle", bundled["mdi:triangle"]);

const spans = [
  {
    observation_span: {
      id: "root",
      name: "Support Agent v3",
      observation_type: "agent",
      start_time: "2026-03-29T10:00:00.000Z",
      latency_ms: 1000,
      status: "OK",
    },
    children: [
      {
        observation_span: {
          id: "s1",
          name: "classify_intent",
          observation_type: "llm",
          start_time: "2026-03-29T10:00:00.100Z",
          latency_ms: 400,
          status: "OK",
        },
        children: [],
      },
      {
        observation_span: {
          id: "s2",
          name: "lookup_order",
          observation_type: "tool",
          start_time: "2026-03-29T10:00:00.500Z",
          latency_ms: 500,
          status: "ERROR",
        },
        children: [],
      },
    ],
  },
];

describe("TraceTreeV2 annotations", () => {
  it("renders nothing extra without annotations", () => {
    const { container } = render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId={null}
        onSelectSpan={vi.fn()}
      />,
    );
    expect(container.querySelectorAll("[data-span-annotation]")).toHaveLength(
      0,
    );
  });

  it("renders chips after the annotated span's name", () => {
    const { container } = render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId={null}
        onSelectSpan={vi.fn()}
        annotations={{
          s1: [
            { tone: "error", label: "First wrong step", icon: "mdi:triangle" },
          ],
          s2: [
            { tone: "warning", label: "retry 2" },
            { tone: "error", label: "order_found" },
          ],
        }}
        highlightSpanIds={["s2"]}
      />,
    );
    const chips = [...container.querySelectorAll("[data-span-annotation]")].map(
      (n) => n.textContent,
    );
    expect(container.querySelector("[data-missing-icon]")).toBeNull();
    expect(chips).toEqual(["First wrong step", "retry 2", "order_found"]);
    expect(screen.getByText("classify_intent")).toBeInTheDocument();
  });
});

describe("SpanTreeTimeline axisRangeMs", () => {
  const labels = (container) =>
    [...container.querySelectorAll("*")]
      .filter((n) => n.children.length === 0 && /^\d/.test(n.textContent))
      .map((n) => n.textContent);

  it("uses the trace's own range without the prop", () => {
    const { container } = render(
      <SpanTreeTimeline
        spans={spans}
        selectedSpanId={null}
        onSelectSpan={vi.fn()}
      />,
    );
    expect(labels(container)).toContain("1.0s");
    expect(labels(container)).not.toContain("4.0s");
  });

  it("draws on the shared axis when given", () => {
    const { container } = render(
      <SpanTreeTimeline
        spans={spans}
        selectedSpanId={null}
        onSelectSpan={vi.fn()}
        axisRangeMs={{ duration: 4000 }}
      />,
    );
    expect(labels(container)).toContain("4.0s");
  });
});
