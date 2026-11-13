import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { render } from "src/utils/test-utils";
import SmartPreview from "src/components/traceDetail/SmartPreview";
import SpanAttachments, { SpanAttachmentsProvider } from "../SpanAttachments";
import { resolveAttachments } from "../client";
import { projectId, readyGrant, spanImage } from "./fixtures";

vi.mock("../client", () => ({ resolveAttachments: vi.fn() }));

const traceId = "30000000-0000-0000-0000-000000000003";
const spanId = "4000000000000004";
const EmptyCard = () => null;
const span = (attachments) => ({
  id: spanId,
  trace: traceId,
  name: "llm",
  observation_type: "llm",
  start_time: "2026-11-11T10:00:00Z",
  attachments,
});
const chat = (count) =>
  JSON.stringify(
    Array.from({ length: count }, (_, i) => ({
      role: i % 2 ? "assistant" : "user",
      content: `message text ${i + 1}`,
    })),
  );
const image = (index, reference) => spanImage(index, { reference });

function View({ attachments, input = chat(2), viewMode = "chat", enabled }) {
  const value = span(attachments);
  return (
    <SpanAttachmentsProvider projectId={projectId} span={value} enabled={enabled}>
      <SmartPreview
        span={value}
        input={input}
        output={JSON.stringify({ role: "assistant", content: "the reply" })}
        attributes={{}}
        viewMode={viewMode}
        ContentCard={EmptyCard}
        AttributesCard={EmptyCard}
        JsonPreviewBlock={EmptyCard}
      />
      {enabled !== false && (
        <SpanAttachments projectId={projectId} span={value} allSpans={[]} />
      )}
    </SpanAttachmentsProvider>
  );
}

View.propTypes = {
  attachments: PropTypes.array,
  input: PropTypes.string,
  viewMode: PropTypes.string,
  enabled: PropTypes.bool,
};

const list = () => screen.queryByLabelText("Span attachments");

beforeEach(() => {
  resolveAttachments.mockImplementation(async (_, items) =>
    items.map((item) => readyGrant(item.occurrence_id)),
  );
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("images at their message position (U01)", () => {
  it("shows a message's images inside that message and lists only the rest, in ONE resolve", async () => {
    render(
      <View
        attachments={[
          image(0, { message_index: 0, part_index: 1, role: "user" }),
          image(1, { direction: "output", message_index: 0, part_index: 0 }),
          image(2, { message_index: 7, part_index: 0 }),
        ]}
      />,
    );
    const groups = await screen.findAllByLabelText("Message images");
    expect(groups).toHaveLength(2);
    const [inputImages, outputImages] = groups;
    expect(
      within(inputImages).getByRole("img", {
        name: "Input · message 1 · part 2 image",
      }),
    ).toBeInTheDocument();
    expect(
      within(outputImages).getByRole("img", {
        name: "Output · message 1 · part 1 image",
      }),
    ).toBeInTheDocument();
    // The input images sit in the first user message, after its text.
    const card = inputImages.parentElement;
    expect(within(card).getByText("message text 1")).toBeInTheDocument();
    expect(within(card).queryByText("message text 2")).toBeNull();
    // message_index 7 has no rendered message: it stays in the list.
    expect(screen.getByText("Attachments")).toBeInTheDocument();
    expect(screen.getByText("Input · message 8 · part 1")).toBeInTheDocument();
    expect(screen.queryByText("Input · message 1 · part 2")).toBeNull();
    expect(screen.getAllByRole("img")).toHaveLength(3);
    expect(resolveAttachments).toHaveBeenCalledTimes(1);
    expect(resolveAttachments.mock.calls[0][1]).toHaveLength(3);
  });

  it("lists an image whose role disagrees with the message instead of misplacing it", async () => {
    render(
      <View
        attachments={[image(0, { message_index: 1, part_index: 0, role: "user" })]}
      />,
    );
    // Input message 2 is the assistant's.
    expect(
      await screen.findByText("Input · message 2 · part 1"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Message images")).toBeNull();
    expect(await screen.findByRole("img")).toBeInTheDocument();
  });

  it("lists images of collapsed messages and moves them inline when expanded", async () => {
    render(
      <View
        input={chat(6)}
        attachments={[image(0, { message_index: 2, part_index: 0 })]}
      />,
    );
    expect(
      await screen.findByText("Input · message 3 · part 1"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Message images")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Show 3 more/ }));
    const inline = await screen.findByLabelText("Message images");
    expect(within(inline.parentElement).getByText("message text 3")).toBeVisible();
    expect(screen.queryByText("Input · message 3 · part 1")).toBeNull();
    expect(screen.queryByText("Attachments")).toBeNull();
    // Moving between list and message never re-resolves the batch.
    expect(resolveAttachments).toHaveBeenCalledTimes(1);
  });

  it("keeps everything in the list in JSON view", async () => {
    render(
      <View
        viewMode="json"
        attachments={[image(0, { message_index: 0, part_index: 0 })]}
      />,
    );
    expect(
      await screen.findByText("Input · message 1 · part 1"),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Message images")).toBeNull();
  });

  it("renders and requests nothing when the provider is disabled (read-only)", () => {
    render(
      <View
        enabled={false}
        attachments={[image(0, { message_index: 0, part_index: 0 })]}
      />,
    );
    expect(screen.getByText("message text 1")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(resolveAttachments).not.toHaveBeenCalled();
    expect(list()).toBeNull();
  });
});
