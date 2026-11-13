import { describe, expect, it } from "vitest";
import {
  recordingProjectionSchema,
  resolveItemSchema,
  spanAttachmentSchema,
} from "../contract";
import {
  attachmentKind,
  collectScreenshots,
  groupByPosition,
  inlineImagesFor,
  positionLabel,
  readSpanAttachments,
  spanParent,
} from "../attachmentPlacement";
import { mixedManifest, occurrenceId, parent, spanImage } from "./fixtures";

const traceId = "30000000-0000-0000-0000-000000000003";
const spanEntry = (id, start, attachments, children = []) => ({
  observation_span: {
    id,
    trace: traceId,
    name: `step-${id.slice(-2)}`,
    start_time: start,
    attachments,
  },
  children,
});
const shot = (index, extra = {}) =>
  spanImage(index, {
    reference: {
      purpose: "computer_screenshot",
      direction: "output",
      ...extra,
    },
  });

describe("I04 Voice projection as shipped by tracer view_enrichment", () => {
  it.each([
    [{ state: "rejected" }],
    [{ occurrence_id: occurrenceId, parent, state: "waiting" }],
    [{ occurrence_id: occurrenceId, parent, state: "pending" }],
    [
      {
        occurrence_id: occurrenceId,
        parent,
        state: "ready",
        manifest: mixedManifest,
      },
    ],
  ])("accepts %j", (value) => {
    expect(recordingProjectionSchema.safeParse(value).success).toBe(true);
  });
  it.each([
    [
      "ready without a manifest",
      { occurrence_id: occurrenceId, parent, state: "ready" },
    ],
    [
      "a manifest on a coarse state",
      {
        occurrence_id: occurrenceId,
        parent,
        state: "pending",
        manifest: mixedManifest,
      },
    ],
    [
      "an origin label",
      {
        occurrence_id: occurrenceId,
        parent,
        state: "ready",
        origin: "pipecat",
        manifest: mixedManifest,
      },
    ],
    [
      "a URL",
      {
        occurrence_id: occurrenceId,
        parent,
        state: "pending",
        url: "https://bucket.example/x.wav",
      },
    ],
    [
      "an unknown state",
      { occurrence_id: occurrenceId, parent, state: "disabled" },
    ],
  ])("rejects %s", (_, value) => {
    expect(recordingProjectionSchema.safeParse(value).success).toBe(false);
  });
});

describe("per-span attachment projection", () => {
  it("accepts the coarse statuses the reader emits and rejects grants or URLs", () => {
    for (const status of [
      "pending",
      "pending_upload",
      "importing",
      "validating",
      "ready",
      "partial",
      "failed",
    ])
      expect(
        spanAttachmentSchema.safeParse(spanImage(0, { status })).success,
      ).toBe(true);
    expect(
      spanAttachmentSchema.safeParse(spanImage(0, { status: "deleted" }))
        .success,
    ).toBe(false);
    expect(
      spanAttachmentSchema.safeParse({ ...spanImage(0), gateway_path: "/x" })
        .success,
    ).toBe(false);
    expect(
      spanAttachmentSchema.safeParse(
        spanImage(0, { reference: { mime_type: "image/svg+xml" } }),
      ).success,
    ).toBe(false);
  });
  it("drops invalid rows and never reads more than 64", () => {
    const rows = [
      spanImage(0),
      { reference: {}, status: "ready" },
      ...Array.from({ length: 80 }, (_, i) => spanImage(i + 1)),
    ];
    const items = readSpanAttachments({ attachments: rows });
    expect(items).toHaveLength(63);
    expect(readSpanAttachments({ attachments: "nope" })).toEqual([]);
    expect(readSpanAttachments({})).toEqual([]);
  });
  it("requests originals for images and files only through F01 representations", () => {
    expect(
      resolveItemSchema.safeParse({
        occurrence_id: occurrenceId,
        parent,
        representation: "original",
      }).success,
    ).toBe(true);
    expect(
      resolveItemSchema.safeParse({
        occurrence_id: occurrenceId,
        parent,
        representation: "thumbnail",
      }).success,
    ).toBe(false);
  });
  it("builds the resolver parent only from the span's own ids", () => {
    expect(spanParent({ id: "4000000000000004", trace: traceId })).toEqual({
      trace_id: traceId,
      span_id: "4000000000000004",
    });
    expect(spanParent({ id: "0000000000000000", trace: traceId })).toBeNull();
    expect(spanParent({ id: "4000000000000004" })).toBeNull();
  });
});

describe("placement by message position", () => {
  it("labels positions 1-based from the reference", () => {
    expect(
      positionLabel(
        spanImage(1, { reference: { message_index: 2 } }).reference,
      ),
    ).toBe("Input · message 3 · part 2");
    expect(
      positionLabel({
        direction: "output",
        role: "tool",
        tool_call_id: "toolu_1",
      }),
    ).toBe("Tool result · call toolu_1");
  });
  it("orders input before output and messages before later messages", () => {
    const out = spanImage(0, {
      reference: { direction: "output", message_index: 0 },
    });
    const second = spanImage(1, { reference: { message_index: 1 } });
    const first = spanImage(2, { reference: { message_index: 0 } });
    expect(
      groupByPosition([out, second, first]).map((group) => group.label),
    ).toEqual([
      "Input · message 1 · part 3",
      "Input · message 2 · part 2",
      "Output · message 1 · part 1",
    ]);
  });
  it("classifies kinds without trusting file names", () => {
    expect(attachmentKind(shot(0).reference)).toBe("screenshot");
    expect(attachmentKind(spanImage(0).reference)).toBe("image");
    expect(attachmentKind({ modality: "video" })).toBe("media");
    expect(attachmentKind({ modality: "document" })).toBe("file");
  });
});

describe("computer-use filmstrip ordering", () => {
  it("is chronological across nested spans and keeps identical-pixel steps distinct", () => {
    const tree = [
      spanEntry(
        "1000000000000002",
        "2026-11-11T10:00:02Z",
        [shot(2)],
        [
          spanEntry("1000000000000001", "2026-11-11T10:00:01Z", [
            shot(1),
            shot(0, { message_index: 0, part_index: 0 }),
          ]),
        ],
      ),
      spanEntry("1000000000000003", "2026-11-11T10:00:03Z", [
        shot(3),
        spanImage(9),
      ]),
    ];
    const frames = collectScreenshots(tree);
    expect(
      frames.map((frame) => frame.item.reference.occurrence_id.slice(-1)),
    ).toEqual(["1", "2", "3", "4"]);
    expect(frames[0].parent).toEqual({
      trace_id: traceId,
      span_id: "1000000000000001",
    });
  });
  it("lists the same occurrence once per span", () => {
    const frames = collectScreenshots([
      spanEntry("1000000000000001", "2026-11-11T10:00:01Z", [shot(0), shot(0)]),
    ]);
    expect(frames).toHaveLength(1);
  });
});

describe("in-message image placement", () => {
  const at = (index, reference) => spanImage(index, { reference });
  it("returns a message's own images in part order", () => {
    const items = [
      at(0, { message_index: 1, part_index: 2 }),
      at(1, { message_index: 1, part_index: 0 }),
      at(2, { message_index: 0 }),
      at(3, { message_index: 1, direction: "output" }),
    ];
    expect(
      inlineImagesFor(items, {
        direction: "input",
        messageIndex: 1,
        role: "user",
      }).map((item) => item.reference.occurrence_id.slice(-1)),
    ).toEqual(["2", "1"]);
    expect(
      inlineImagesFor(items, { direction: "output", messageIndex: 1 }),
    ).toEqual([items[3]]);
  });
  it("keeps an image whose role disagrees with the message out of it", () => {
    const items = [at(0, { message_index: 0, role: "tool" })];
    expect(
      inlineImagesFor(items, {
        direction: "input",
        messageIndex: 0,
        role: "user",
      }),
    ).toEqual([]);
    expect(
      inlineImagesFor(items, {
        direction: "input",
        messageIndex: 0,
        role: "tool",
      }),
    ).toEqual(items);
  });
  it("maps provider role names onto F01 roles", () => {
    const items = [at(0, { message_index: 0, role: "assistant" })];
    for (const role of ["assistant", "model", "ai", "Assistant"])
      expect(
        inlineImagesFor(items, { direction: "input", messageIndex: 0, role }),
      ).toEqual(items);
    expect(
      inlineImagesFor(items, {
        direction: "input",
        messageIndex: 0,
        role: "user",
      }),
    ).toEqual([]);
  });
  it("never inlines screenshots, other media, mixed direction or unplaced images", () => {
    const items = [
      shot(0, { message_index: 0, direction: "input" }),
      at(1, { message_index: 0, modality: "audio", mime_type: "audio/wav" }),
      at(2, { message_index: 0, direction: "mixed" }),
      at(3, { message_index: undefined }),
    ];
    for (const direction of ["input", "mixed"])
      expect(
        inlineImagesFor(items, { direction, messageIndex: 0, role: "user" }),
      ).toEqual([]);
    expect(
      inlineImagesFor([at(0)], { direction: "input", messageIndex: -1 }),
    ).toEqual([]);
  });
});

describe("computer-use filmstrip ordering by step, placement, then time", () => {
  const ids = (frames) =>
    frames.map((frame) => frame.item.reference.occurrence_id.slice(-1));
  it("keeps a tool span's unplaced screenshot between the steps around it", () => {
    // LLM step 1 (placed), MCP screenshot tool (no position), LLM step 2.
    const frames = collectScreenshots([
      spanEntry("1000000000000001", "2026-11-11T10:00:01Z", [
        shot(0, { message_index: 2, part_index: 0 }),
      ]),
      spanEntry("1000000000000002", "2026-11-11T10:00:02Z", [
        shot(1, { message_index: undefined, part_index: undefined, role: "tool" }),
      ]),
      spanEntry("1000000000000003", "2026-11-11T10:00:03Z", [
        shot(2, { message_index: 4, part_index: 0 }),
      ]),
    ]);
    expect(ids(frames)).toEqual(["1", "2", "3"]);
  });
  it("orders one span's screenshots by message and part, not listing order", () => {
    const frames = collectScreenshots([
      spanEntry("1000000000000001", "2026-11-11T10:00:01Z", [
        shot(0, { direction: "input", message_index: 5, part_index: 0 }),
        shot(1, { direction: "input", message_index: 1, part_index: 1 }),
        shot(2, { direction: "input", message_index: 1, part_index: 0 }),
      ]),
    ]);
    expect(ids(frames)).toEqual(["3", "2", "1"]);
  });
  it("orders a conversation turn by placement across its spans, then the next turn", () => {
    const turn = (id) => ({ conversation_id: "conv-1", turn_id: id });
    const frames = collectScreenshots([
      spanEntry("1000000000000001", "2026-11-11T10:00:01Z", [
        shot(0, { ...turn("t1"), direction: "input", message_index: 3 }),
      ]),
      // Same turn, later span, earlier message position.
      spanEntry("1000000000000002", "2026-11-11T10:00:02Z", [
        shot(1, { ...turn("t1"), direction: "input", message_index: 1 }),
      ]),
      spanEntry("1000000000000003", "2026-11-11T10:00:03Z", [
        shot(2, { ...turn("t2"), direction: "input", message_index: 0 }),
      ]),
    ]);
    expect(ids(frames)).toEqual(["2", "1", "3"]);
  });
  it("never compares message positions across different steps", () => {
    // A later span with a low message_index must not jump ahead in time.
    const frames = collectScreenshots([
      spanEntry("1000000000000001", "2026-11-11T10:00:01Z", [
        shot(0, { message_index: 9 }),
      ]),
      spanEntry("1000000000000002", "2026-11-11T10:00:02Z", [
        shot(1, { message_index: 0 }),
      ]),
    ]);
    expect(ids(frames)).toEqual(["1", "2"]);
  });
});
