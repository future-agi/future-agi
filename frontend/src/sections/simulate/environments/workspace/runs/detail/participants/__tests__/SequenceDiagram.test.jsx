import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import SequenceDiagram from "../SequenceDiagram";
import { participantsFromRecord } from "../participantsFromRecord";
import { sequenceOf } from "../sequenceOf";
import {
  DELEGATED,
  LOOKUP,
  RETURNED,
  callStep,
  handOffStep,
  timeline,
} from "../__fixtures__/workerRecord";

const seqOf = (record) =>
  sequenceOf(participantsFromRecord(record), { callerName: "Isabela" });

const draw = (seq, props = {}) => {
  const onSelect = vi.fn();
  const view = render(
    <SequenceDiagram
      seq={seq}
      selected={null}
      onSelect={onSelect}
      {...props}
    />,
  );
  return { ...view, onSelect };
};

const stepRow = (n) => document.querySelector(`[data-row="${n}"]`);

// Turn 1 answered plainly, turn 2 a worker retrying one tool 12 times.
const longRecord = () => {
  const retries = Array.from({ length: 12 }, (_, i) => ({
    ...LOOKUP,
    seq: 20 + i,
    at: `2026-10-31T12:00:${String(20 + i).padStart(2, "0")}+00:00`,
  }));
  const asked = { ...DELEGATED, seq: 10, at: "2026-10-31T12:00:15+00:00" };
  const steps = [
    {
      ...handOffStep(10, {
        worker_id: "r/order_lookup",
        handoff: asked,
        end: "no_return",
        children: retries.map((r) => r.seq),
      }),
      turn: 2,
    },
    ...retries.map((r) => ({
      ...callStep(r, { parent: 10, depth: 1 }),
      turn: 2,
    })),
  ];
  return timeline({
    steps,
    turns: [
      {
        turn: 1,
        request: { text: "Hi", at: "2026-10-31T11:59:54+00:00" },
        response: { text: "Hello", at: "2026-10-31T11:59:58+00:00" },
        children: [],
        calls_outside: 0,
        expects_messages: true,
      },
      {
        turn: 2,
        request: { text: "Refund it", at: "2026-10-31T12:00:10+00:00" },
        response: { text: "Sorry", at: "2026-10-31T12:00:40+00:00" },
        children: [10],
        calls_outside: 0,
        expects_messages: true,
      },
    ],
  });
};

describe("SequenceDiagram", () => {
  it("numbers each request on its arrow and labels replies with the step they answer", () => {
    draw(seqOf(timeline()));
    expect(stepRow(2)).toHaveAttribute(
      "aria-label",
      "Step 2: inspect order 1042",
    );
    expect(screen.getByText(/reply to step 2/)).toBeInTheDocument();
  });

  it("arrow text is the start of the exact message", () => {
    draw(seqOf(timeline()));
    expect(screen.getByText(/inspect order 1042/)).toBeInTheDocument();
  });

  it("Fold tool calls hides tool rows and keeps step numbers", () => {
    draw(seqOf(timeline()));
    expect(stepRow(3)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Fold tool calls/ }));
    expect(stepRow(3)).toBeNull();
    expect(stepRow(2)).toHaveAttribute(
      "aria-label",
      expect.stringMatching(/^Step 2:/),
    );
  });

  it("Problems only keeps the problems and the steps that led to them", () => {
    draw(seqOf(timeline({ returned: null, end: "no_return" })));
    fireEvent.click(screen.getByRole("button", { name: /Problems only/ }));
    expect(stepRow(2)).toBeInTheDocument();
    expect(stepRow(4)).toBeNull();
  });

  it("clicking a lane name fades every step that lane isn't part of", () => {
    draw(seqOf(timeline()));
    fireEvent.click(
      screen.getByRole("button", { name: "Follow Order-lookup worker" }),
    );
    expect(stepRow(2).style.opacity).toBe("1");
    expect(stepRow(4).style.opacity).toBe("0.22");
  });

  it("lists declared workers that didn't run under the diagram", () => {
    draw(seqOf(timeline()));
    expect(
      screen.getByText(/Declared, didn't run: Audit worker/),
    ).toBeInTheDocument();
  });

  it("selecting a step shows the exact message asked and answered", () => {
    draw(seqOf(timeline()), { selected: 2 });
    const detail = screen.getByTestId("step-detail");
    expect(within(detail).getByText("inspect order 1042")).toBeInTheDocument();
    expect(within(detail).getByText("eligible")).toBeInTheDocument();
  });

  it("a tool step without a duration shows no Took line", () => {
    draw(seqOf(timeline()), { selected: 3 });
    expect(
      within(screen.getByTestId("step-detail")).queryByText(/^Took/),
    ).toBeNull();
  });

  it("Open in trace appears only with a handler and a trace on the step", () => {
    const traced = { ...DELEGATED, span_id: "s1", trace_id: "t1" };
    const seq = seqOf(
      timeline({
        steps: [
          handOffStep(1, {
            worker_id: "r/order_lookup",
            handoff: traced,
            end: "no_return",
          }),
        ],
      }),
    );
    const onOpenTrace = vi.fn();
    const { rerender } = draw(seq, { selected: 2 });
    expect(screen.queryByRole("button", { name: /Open in trace/ })).toBeNull();
    rerender(
      <SequenceDiagram
        seq={seq}
        selected={2}
        onSelect={vi.fn()}
        onOpenTrace={onOpenTrace}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Open in trace/ }));
    expect(onOpenTrace).toHaveBeenCalledWith("t1", "s1");
  });

  it("Open in trace stays hidden for a step with no trace", () => {
    draw(seqOf(timeline()), { selected: 2, onOpenTrace: vi.fn() });
    expect(screen.queryByRole("button", { name: /Open in trace/ })).toBeNull();
  });

  it("Show in transcript sends the step's turn and seconds", () => {
    const onShowInTranscript = vi.fn();
    draw(seqOf(timeline()), { selected: 2, onShowInTranscript });
    fireEvent.click(screen.getByRole("button", { name: /Show in transcript/ }));
    expect(onShowInTranscript).toHaveBeenCalledWith({
      turn: 1,
      at: expect.closeTo(5.9, 3),
    });
  });

  it("a cancelled worker's step shows its end state", () => {
    const seq = seqOf(
      timeline({
        steps: [
          {
            ...handOffStep(1, {
              worker_id: "r/order_lookup",
              handoff: DELEGATED,
              end: "cut_off",
            }),
            end_state: "cancelled",
          },
        ],
      }),
    );
    draw(seq, { selected: 2 });
    expect(screen.getByTestId("step-end-state")).toHaveTextContent(
      "Cut short · cancelled",
    );
  });

  it("a call over 12 steps groups by turn and only problem turns start open", () => {
    draw(seqOf(longRecord()));
    expect(
      screen.getByRole("button", { name: "Open turn 1" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Fold turn 2" }),
    ).toBeInTheDocument();
    expect(stepRow(1)).toBeNull();
  });

  it("a tool repeated 3 or more times in a turn is one loop row", () => {
    draw(seqOf(longRecord()));
    expect(screen.getByText(/↻ 12 times/)).toBeInTheDocument();
    expect(stepRow(5)).toBeNull();
  });

  it("shows a message the API marks as prose as formatted text", () => {
    const answered = {
      ...RETURNED,
      result: "**Eligible** for a refund",
      result_shown: { format: "text", text: "**Eligible** for a refund" },
    };
    draw(seqOf(timeline({ returned: answered })), { selected: 2 });
    const detail = screen.getByTestId("step-detail");
    expect(within(detail).getByText("Eligible").tagName).toBe("STRONG");
  });

  it("shows a tool's object result in the JSON viewer", () => {
    const call = {
      ...LOOKUP,
      status: "ok",
      output: { refund_status: "issued" },
    };
    const steps = [
      handOffStep(1, {
        worker_id: "r/order_lookup",
        handoff: DELEGATED,
        returned: RETURNED,
        end: "finished",
        children: [call.seq],
      }),
      callStep(call, { parent: 1, depth: 1 }),
    ];
    draw(seqOf(timeline({ steps })), { selected: 3 });
    expect(screen.getByTestId("tool-result")).toHaveTextContent(
      "refund_status",
    );
  });

  it("reads escaped line breaks in a tool's text result as line breaks", () => {
    const call = {
      ...LOOKUP,
      status: "ok",
      output: "first line\\nsecond line",
    };
    const steps = [
      handOffStep(1, {
        worker_id: "r/order_lookup",
        handoff: DELEGATED,
        returned: RETURNED,
        end: "finished",
        children: [call.seq],
      }),
      callStep(call, { parent: 1, depth: 1 }),
    ];
    draw(seqOf(timeline({ steps })), { selected: 3 });
    expect(screen.getByTestId("tool-result").textContent).toBe(
      "first line\nsecond line",
    );
  });

  it("shows a message the API marks as data in the JSON viewer", () => {
    const asked = {
      ...DELEGATED,
      task: '{"description": "fix it"}',
      task_shown: { format: "code", text: '{\n  "description": "fix it"\n}' },
    };
    draw(
      seqOf(
        timeline({
          steps: [
            handOffStep(1, {
              worker_id: "r/order_lookup",
              handoff: asked,
              end: "no_return",
            }),
          ],
        }),
      ),
      { selected: 2 },
    );
    expect(screen.getByTestId("message-json")).toHaveTextContent("description");
  });

  it("shows a tool's JSON text result in the JSON viewer", () => {
    const call = {
      ...LOOKUP,
      status: "ok",
      output: '{"refund_status": "issued"}',
    };
    const steps = [
      handOffStep(1, {
        worker_id: "r/order_lookup",
        handoff: DELEGATED,
        returned: RETURNED,
        end: "finished",
        children: [call.seq],
      }),
      callStep(call, { parent: 1, depth: 1 }),
    ];
    draw(seqOf(timeline({ steps })), { selected: 3 });
    expect(screen.getByTestId("tool-result").tagName).toBe("DIV");
    expect(screen.getByTestId("tool-result")).toHaveTextContent(
      "refund_status",
    );
  });
});
