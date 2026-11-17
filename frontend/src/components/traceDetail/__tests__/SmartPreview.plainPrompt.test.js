import { createElement } from "react";
import PropTypes from "prop-types";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const prompt = "Find the weather in Oslo.";
const reply = "It is sunny in Oslo.";
const toolArguments = '{"city":"Oslo"}';
const toolCallOutput = {
  role: "assistant",
  content: null,
  tool_calls: [
    {
      id: "call-weather-1",
      function: { name: "lookup_weather", arguments: toolArguments },
    },
  ],
};
const out = (field) => `llm.output_messages.0.message.${field}`;
// Only the tool call is recorded as an output-message attribute: there are no
// input-message attributes, and input.value is the prompt itself.
const toolCallAttributes = {
  [out("role")]: "assistant",
  [out("tool_calls.0.tool_call.id")]: "call-weather-1",
  [out("tool_calls.0.tool_call.function.name")]: "lookup_weather",
  [out("tool_calls.0.tool_call.function.arguments")]: toolArguments,
};
const EmptyCard = () => null;
// Observable stand-in for the drawer's raw Input / Output card. Title and
// content are separate elements, so a prompt shown there is found by its text.
const RawContentCard = ({ title, content }) =>
  createElement(
    "section",
    null,
    createElement("h6", null, title),
    createElement(
      "pre",
      null,
      typeof content === "string" ? content : JSON.stringify(content),
    ),
  );
RawContentCard.propTypes = { title: PropTypes.string, content: PropTypes.any };
const preview = (props) =>
  render(
    createElement(SmartPreview, {
      span: { observation_type: "llm" },
      viewMode: "markdown",
      ContentCard: RawContentCard,
      AttributesCard: EmptyCard,
      JsonPreviewBlock: EmptyCard,
      ...props,
    }),
  );

// Routing, attribute extraction, chat normalization, Markdown and tool cards
// are real. Only icons and unrelated caller-supplied panels are stubbed.
describe("SmartPreview plain prompt with output-message attributes", () => {
  it("keeps a plain-text prompt visible next to a tool-call-only output message", () => {
    const { container } = preview({
      input: prompt,
      output: toolCallOutput,
      attributes: toolCallAttributes,
    });

    expect(screen.getByText(prompt)).toBeInTheDocument();
    fireEvent.click(screen.getByText("lookup_weather"));
    expect(screen.getByText(toolArguments)).toBeInTheDocument();
    const text = container.textContent;
    expect(text.indexOf(prompt)).toBeLessThan(text.indexOf("lookup_weather"));
  });

  it.each([
    { label: "decoded", input: { question: prompt } },
    { label: "JSON-encoded", input: JSON.stringify({ question: prompt }) },
  ])(
    "keeps a $label non-message prompt visible next to a tool-call-only output message",
    ({ input }) => {
      const { container } = preview({
        input,
        output: toolCallOutput,
        attributes: toolCallAttributes,
      });

      expect(screen.getByText("lookup_weather")).toBeInTheDocument();
      const text = container.textContent;
      expect(text).toContain(prompt);
      expect(text.indexOf(prompt)).toBeLessThan(text.indexOf("lookup_weather"));
    },
  );

  it("keeps a plain-text prompt visible next to a text output message", () => {
    const { container } = preview({
      input: prompt,
      output: reply,
      attributes: { [out("role")]: "assistant", [out("content")]: reply },
    });

    expect(screen.getByText(prompt)).toBeInTheDocument();
    expect(screen.getByText(reply)).toBeInTheDocument();
    const text = container.textContent;
    expect(text.indexOf(prompt)).toBeLessThan(text.indexOf(reply));
  });

  it("does not add the joined input text when input-message attributes exist", () => {
    const { container } = preview({
      input: `System: Answer briefly.\nUser: ${prompt}`,
      output: toolCallOutput,
      attributes: {
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": prompt,
        ...toolCallAttributes,
      },
    });

    expect(screen.getAllByText(prompt)).toHaveLength(1);
    expect(container.textContent).not.toContain("Answer briefly.");
    expect(screen.getByText("lookup_weather")).toBeInTheDocument();
  });
});
