import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import TraceTreeV2 from "../TraceTreeV2";
import { isTimingSpan, withoutTimingSpans } from "../timingSpans";
import { getTypeConfig } from "../spanTypeConfig";

// jsdom does not implement the browser's scrolling API.
const originalScroll = Object.getOwnPropertyDescriptor(Element.prototype, "scrollIntoView");
beforeAll(() => Object.defineProperty(Element.prototype, "scrollIntoView", {
  configurable: true, value: vi.fn(),
}));
afterAll(() => {
  if (originalScroll) Object.defineProperty(Element.prototype, "scrollIntoView", originalScroll);
  else delete Element.prototype.scrollIntoView;
});

const timing = {
  id: "timing",
  name: "session.timing.model_resolution",
  observation_type: "internal",
  status: "OK",
  span_attributes: { "fi.observation.category": "timing" },
};
const spans = [
  {
    observation_span: {
      id: "agent",
      name: "invoke_agent",
      observation_type: "agent",
      status: "OK",
    },
    children: [
      {
        observation_span: timing,
        children: [
          {
            observation_span: {
              id: "llm",
              name: "model request",
              observation_type: "llm",
              total_tokens: 193,
            },
            children: [],
          },
        ],
      },
    ],
  },
];

describe("qualified timing spans", () => {
  it("hides timing by default, promotes real work and toggles without mutation", () => {
    const before = JSON.stringify(spans);
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="agent"
        onSelectSpan={vi.fn()}
      />,
    );
    expect(screen.queryByText(timing.name)).not.toBeInTheDocument();
    expect(screen.getByText("model request")).toBeInTheDocument();
    const show = screen.getByRole("button", { name: "Show 1 timing span" });
    expect(show).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(show);
    expect(screen.getByText(timing.name)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide 1 timing span" }));
    expect(screen.queryByText(timing.name)).not.toBeInTheDocument();
    expect(JSON.stringify(spans)).toBe(before);
  });

  it("shares visibility changes with the companion graph", () => {
    const changed = vi.fn();
    const { rerender } = render(<TraceTreeV2 spans={spans} onSelectSpan={vi.fn()}
      showTimingSpans={false} onTimingVisibilityChange={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "Show 1 timing span" }));
    expect(changed).toHaveBeenLastCalledWith(true);
    rerender(<TraceTreeV2 spans={spans} onSelectSpan={vi.fn()}
      showTimingSpans onTimingVisibilityChange={changed} />);
    expect(screen.getByRole("region", { name: "Trace Tree" })).toBeInTheDocument();
    expect(screen.getByText(timing.name)).toBeInTheDocument();
  });

  it("reveals a directly selected timing span", () => {
    render(
      <TraceTreeV2
        spans={spans}
        selectedSpanId="timing"
        onSelectSpan={vi.fn()}
      />,
    );
    expect(screen.getByText(timing.name)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Hide 1 timing span" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it.each([
    { status: "ERROR" },
    { total_tokens: 1 },
    { prompt_tokens: 1 },
    { completion_tokens: 1 },
    { cost: 0.1 },
    { observation_type: "llm" },
    { span_attributes: {} },
  ])(
    "keeps errors, billable work and unqualified activity visible: %j",
    (change) => {
      expect(isTimingSpan({ ...timing, ...change })).toBe(false);
    },
  );

  it("preserves full child subtree beneath multiple timing-only wrappers", () => {
    const filtered = withoutTimingSpans(spans);
    expect(filtered[0].children.map((e) => e.observation_span.id)).toEqual([
      "llm",
    ]);
    expect(spans[0].children[0].observation_span.id).toBe("timing");
  });

  it("does not show a toggle when there is no timing telemetry", () => {
    render(
      <TraceTreeV2 spans={withoutTimingSpans(spans)} onSelectSpan={vi.fn()} />,
    );
    expect(
      screen.queryByRole("button", { name: /timing span/ }),
    ).not.toBeInTheDocument();
    expect(getTypeConfig("agent").label).toBe("Agent");
    expect(getTypeConfig("internal").label).toBe("Internal");
  });
});
