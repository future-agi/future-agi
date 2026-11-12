import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const question = "Where is order A-1001? Here is my receipt photo.";
const receiptUrl = "http://localhost:3100/receipt-photo.svg";
const reply = "Order A-1001 has shipped.";
const msg = (index, field) => `llm.input_messages.${index}.message.${field}`;
// traceAI / OpenInference shape: the conversation lives only in flattened
// message attributes, and input.value is plain joined text.
const flattenedAttributes = {
  [msg(0, "role")]: "user",
  [msg(0, "contents.0.message_content.type")]: "text",
  [msg(0, "contents.0.message_content.text")]: question,
  [msg(0, "contents.1.message_content.type")]: "image",
  [msg(0, "contents.1.message_content.image.image.url")]: receiptUrl,
  [msg(1, "role")]: "assistant",
  [msg(1, "tool_calls.0.tool_call.id")]: "call_walk_1",
  [msg(1, "tool_calls.0.tool_call.function.name")]: "lookup_order",
  [msg(1, "tool_calls.0.tool_call.function.arguments")]:
    '{"order_id": "A-1001"}',
  [msg(2, "role")]: "tool",
  [msg(2, "content")]: '{"status": "shipped"}',
  [msg(2, "tool_call_id")]: "call_walk_1",
  "llm.output_messages.0.message.role": "assistant",
  "llm.output_messages.0.message.content": reply,
};
const EmptyCard = () => null;
const preview = (props) =>
  render(
    createElement(SmartPreview, {
      span: { observation_type: "llm" },
      viewMode: "markdown",
      ContentCard: EmptyCard,
      AttributesCard: EmptyCard,
      JsonPreviewBlock: EmptyCard,
      ...props,
    }),
  );

// Attribute extraction, chat normalization, Markdown and tool cards are real.
describe("SmartPreview flattened message attributes", () => {
  it("OBS-SW-130 renders the text, image, tool call and tool result from llm.input_messages.*", () => {
    const { container } = preview({
      input: `${question}\n${reply}`,
      output: reply,
      attributes: flattenedAttributes,
    });

    expect(screen.getByText(question)).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "Image in this message" }),
    ).toHaveAttribute("src", receiptUrl);
    const text = container.textContent;
    expect(text).not.toContain("{}");
    expect(text).not.toContain("image.url");

    // The assistant turn that only carries the tool call is kept.
    expect(screen.getAllByText("assistant")).toHaveLength(2);
    fireEvent.click(screen.getByText("lookup_order"));
    expect(screen.getByText('{"order_id": "A-1001"}')).toBeInTheDocument();
    expect(screen.getByText("call_walk_1")).toBeInTheDocument();
    expect(screen.getByText('{"status": "shipped"}')).toBeInTheDocument();

    expect(text.indexOf(question)).toBeLessThan(text.indexOf("lookup_order"));
    expect(text.indexOf("lookup_order")).toBeLessThan(
      text.indexOf("call_walk_1"),
    );
    expect(text.indexOf("call_walk_1")).toBeLessThan(text.indexOf(reply));
  });
});
