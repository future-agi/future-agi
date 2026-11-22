import { describe, expect, it } from "vitest";
import { isAttachmentCall } from "../attachmentCall";
import { spanImage } from "src/components/attachments/__tests__/fixtures";

const recording = spanImage(0, {
  reference: {
    modality: "audio",
    mime_type: "audio/ogg",
    purpose: "conversation_recording",
  },
});
const span = (id, extra = {}) => ({
  observation_span: {
    id,
    trace: "trace-1",
    name: id,
    observation_type: "unknown",
    ...extra,
  },
  children: extra.children || [],
});
// LiveKit: the recording is bound on agent_session, a child of the root.
const livekit = (root = {}, session = {}) => [
  span("job_entrypoint", {
    observation_type: "conversation",
    ...root,
    children: [span("agent_session", { attachments: [recording], ...session })],
  }),
];

describe("isAttachmentCall", () => {
  it("is a call when a conversation root has an attached call recording anywhere", () => {
    expect(isAttachmentCall(livekit())).toBe(true);
    // Pipecat binds the recording on the conversation root itself.
    expect(
      isAttachmentCall([
        span("conversation", {
          observation_type: "CONVERSATION",
          attachments: [recording],
        }),
      ]),
    ).toBe(true);
  });

  it("keeps calls without an attached recording on their own view (Vapi, Retell)", () => {
    expect(isAttachmentCall(livekit({}, { attachments: [] }))).toBe(false);
    expect(
      isAttachmentCall([
        span("call", { observation_type: "conversation", recording_url: "x" }),
      ]),
    ).toBe(false);
  });

  it("needs the conversation root Voice Observability reads", () => {
    expect(isAttachmentCall(livekit({ observation_type: "agent" }))).toBe(false);
    expect(
      isAttachmentCall(livekit({ parent_span_id: "outer" })),
    ).toBe(false);
  });

  it("ignores attachments that are not an audio call recording", () => {
    const other = (reference) =>
      livekit({}, { attachments: [spanImage(1, { reference })] });
    expect(isAttachmentCall(other({}))).toBe(false);
    expect(
      isAttachmentCall(
        other({ modality: "audio", mime_type: "audio/wav", purpose: "turn_recording" }),
      ),
    ).toBe(false);
    expect(
      isAttachmentCall(livekit({}, { attachments: [{ reference: {} }] })),
    ).toBe(false);
  });

  it("is false for missing trees", () => {
    expect(isAttachmentCall(undefined)).toBe(false);
    expect(isAttachmentCall([])).toBe(false);
    expect(isAttachmentCall([{}])).toBe(false);
  });
});
