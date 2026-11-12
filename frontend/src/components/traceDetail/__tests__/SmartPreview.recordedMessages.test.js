import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const encodings = [
  { label: "decoded", encode: (value) => value },
  { label: "JSON-encoded", encode: JSON.stringify },
];
const systemText = "You are a math tutor.";
const question = "What's 2+2?";
const geminiAttributes = {
  "llm.input_messages.0.message.role": "system",
  "llm.input_messages.0.message.content": systemText,
  "llm.input_messages.1.message.role": "user",
  "llm.input_messages.1.message.content": question,
};
const geminiUser = { role: "user", parts: [{ text: question }] };
const EmptyCard = () => null;
// Keep the old ContentCard fallback observable instead of mocking it to null.
// eslint-disable-next-line react/prop-types -- test double renders whatever content it is given
const RawContentCard = ({ content }) =>
  createElement(
    "pre",
    null,
    typeof content === "string" ? content : JSON.stringify(content),
  );
const preview = (props) =>
  render(
    createElement(SmartPreview, {
      span: { observation_type: "llm" },
      attributes: {},
      viewMode: "markdown",
      ContentCard: RawContentCard,
      AttributesCard: EmptyCard,
      JsonPreviewBlock: EmptyCard,
      ...props,
    }),
  );

// Routing, attribute extraction, chat normalization, Markdown and tool cards
// are real. Only icons and unrelated caller-supplied panels are stubbed.
describe("SmartPreview recorded messages", () => {
  for (const viewMode of ["markdown", "chat"]) {
    it.each(encodings)(
      `C26-23-CORRECTNESS-1 keeps every $label OpenAI response choice visible in ${viewMode}`,
      ({ encode }) => {
        const { container } = preview({
          viewMode,
          input: encode({ messages: [{ role: "user", content: "Question" }] }),
          output: encode({
            choices: [
              {
                index: 0,
                message: {
                  role: "assistant",
                  content: "First recorded answer",
                },
                finish_reason: "stop",
              },
              {
                index: 1,
                message: {
                  role: "assistant",
                  content: "Second recorded answer",
                },
                finish_reason: "stop",
              },
            ],
          }),
        });
        // Visibility is the contract in both the old raw fallback and chat.
        const text = container.textContent;
        expect(text).toContain("Question");
        expect(text).toContain("First recorded answer");
        expect(text).toContain("Second recorded answer");
        expect(text.indexOf("Question")).toBeLessThan(
          text.indexOf("First recorded answer"),
        );
        expect(text.indexOf("First recorded answer")).toBeLessThan(
          text.indexOf("Second recorded answer"),
        );
      },
    );
  }

  it.each(encodings)(
    "C26-23-CORRECTNESS-1 retains tool metadata in a $label response choice",
    ({ encode }) => {
      preview({
        input: encode({ messages: [{ role: "user", content: "Calculate" }] }),
        output: encode({
          choices: [
            {
              index: 0,
              message: {
                role: "assistant",
                content: null,
                tool_calls: [
                  {
                    id: "choice-call-1",
                    type: "function",
                    function: {
                      name: "add_numbers",
                      arguments: '{"a":2,"b":2}',
                    },
                  },
                ],
              },
              finish_reason: "tool_calls",
            },
          ],
        }),
      });
      fireEvent.click(screen.getByText("add_numbers"));
      expect(screen.getByText('{"a":2,"b":2}')).toBeInTheDocument();
    },
  );

  it("retains output-attribute precedence over a complete response envelope", () => {
    preview({
      input: { messages: [{ role: "user", content: "Question" }] },
      output: {
        choices: [
          { message: { role: "assistant", content: "Raw choice reply" } },
        ],
      },
      attributes: {
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "Attribute reply",
      },
    });
    expect(screen.getByText("Attribute reply")).toBeInTheDocument();
    expect(screen.queryByText("Raw choice reply")).not.toBeInTheDocument();
  });

  for (const instruction of [
    {
      label: "config instruction",
      fields: { config: { system_instruction: systemText } },
    },
    {
      label: "top-level instruction",
      fields: { system_instruction: { parts: [{ text: systemText }] } },
    },
    { label: "attribute-only instruction", fields: {} },
  ]) {
    it.each(encodings)(
      `C26-23-CORRECTNESS-2 retains the system turn for $label Gemini ${instruction.label}`,
      ({ encode }) => {
        const { container } = preview({
          input: encode({ contents: [geminiUser], ...instruction.fields }),
          attributes: geminiAttributes,
        });
        expect(screen.getAllByText(systemText)).toHaveLength(1);
        expect(screen.getAllByText(question)).toHaveLength(1);
        expect(container.textContent.indexOf(systemText)).toBeLessThan(
          container.textContent.indexOf(question),
        );
      },
    );
  }

  it("C26-23-CORRECTNESS-2 retains Gemini system attributes without losing captured tool details", () => {
    const { container } = preview({
      input: {
        contents: [
          geminiUser,
          {
            role: "model",
            parts: [
              {
                functionCall: {
                  name: "sum_values",
                  args: { left: 2, right: 2 },
                },
              },
            ],
          },
          {
            role: "tool",
            parts: [{ text: "Result is four" }],
            name: "sum_result",
            tool_call_id: "sum-call-4",
          },
        ],
        config: { system_instruction: systemText },
      },
      attributes: {
        ...geminiAttributes,
        "llm.input_messages.2.message.role": "model",
        "llm.input_messages.2.message.content": "Lossy tool summary",
        "llm.input_messages.3.message.role": "tool",
        "llm.input_messages.3.message.content": "Result is four",
      },
    });
    expect(screen.getAllByText(systemText)).toHaveLength(1);
    expect(screen.getAllByText(question)).toHaveLength(1);
    expect(screen.getByText("sum_values")).toBeInTheDocument();
    expect(container.textContent).toContain('"left": 2');
    expect(container.textContent).toContain('"right": 2');
    expect(screen.getByText(": sum_result")).toBeInTheDocument();
    expect(screen.getByText("sum-call-4")).toBeInTheDocument();
    expect(screen.getAllByText("Result is four")).toHaveLength(1);
    expect(screen.queryByText("Lossy tool summary")).not.toBeInTheDocument();
  });

  it("keeps a captured Gemini system turn authoritative without duplicating it", () => {
    preview({
      input: {
        contents: [
          { role: "system", parts: [{ text: "Captured system instruction" }] },
          geminiUser,
        ],
      },
      attributes: geminiAttributes,
    });
    expect(screen.getAllByText("Captured system instruction")).toHaveLength(1);
    expect(screen.queryByText(systemText)).not.toBeInTheDocument();
    expect(screen.getAllByText(question)).toHaveLength(1);
  });

  it("keeps the full attribute fallback when Gemini contents are empty", () => {
    preview({ input: { contents: [] }, attributes: geminiAttributes });
    expect(screen.getAllByText(systemText)).toHaveLength(1);
    expect(screen.getAllByText(question)).toHaveLength(1);
  });
});

// LangChain records a chat model's run input as a batch of serialized message
// lists (the shape pinned by insights/tests/test_evidence.py).
const langchainMessage = (className, type, content) => ({
  lc: 1,
  type: "constructor",
  id: ["langchain", "schema", "messages", className],
  kwargs: { content, type },
});
const tutorText = "You are a tutor.";
const sumQuestion = "What is 2+2?";
const otelText = (role, content) => ({
  role,
  parts: [{ type: "text", content }],
});
const briefInstructions = [{ type: "text", content: "Be brief." }];

describe("SmartPreview framework message shapes", () => {
  it.each(encodings)(
    "OBS-SW-137 shows a $label LangChain message batch as system and user turns",
    ({ encode }) => {
      const { container } = preview({
        input: encode({
          messages: [
            [
              langchainMessage("SystemMessage", "system", tutorText),
              langchainMessage("HumanMessage", "human", sumQuestion),
            ],
          ],
        }),
        attributes: {
          "llm.input_messages.0.message.role": "system",
          "llm.input_messages.0.message.content": tutorText,
          "llm.input_messages.1.message.role": "user",
          "llm.input_messages.1.message.content": sumQuestion,
        },
      });

      expect(screen.getByText("system")).toBeInTheDocument();
      expect(screen.getByText("user")).toBeInTheDocument();
      expect(screen.queryByText("unknown")).not.toBeInTheDocument();
      expect(screen.getAllByText(tutorText)).toHaveLength(1);
      expect(screen.getAllByText(sumQuestion)).toHaveLength(1);
      const text = container.textContent;
      expect(text.indexOf(tutorText)).toBeLessThan(text.indexOf(sumQuestion));
      expect(text).not.toContain('"lc"');
    },
  );

  it.each(encodings)(
    "OBS-SW-139 shows $label gen_ai.system_instructions as the first turn",
    ({ encode }) => {
      const { container } = preview({
        input: [otelText("user", "What is this?")],
        attributes: {
          "gen_ai.system_instructions": encode(briefInstructions),
        },
      });

      expect(screen.getByText("system")).toBeInTheDocument();
      expect(screen.getAllByText("Be brief.")).toHaveLength(1);
      const text = container.textContent;
      expect(text.indexOf("Be brief.")).toBeLessThan(
        text.indexOf("What is this?"),
      );
    },
  );

  it("keeps a captured system turn instead of gen_ai.system_instructions", () => {
    preview({
      input: [
        otelText("system", "Captured system"),
        otelText("user", "What is this?"),
      ],
      attributes: {
        "gen_ai.system_instructions": JSON.stringify(briefInstructions),
      },
    });
    expect(screen.getAllByText("Captured system")).toHaveLength(1);
    expect(screen.queryByText("Be brief.")).not.toBeInTheDocument();
  });
});
