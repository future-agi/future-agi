import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ChatMessageView, { isOpenAIMessages } from "../ChatMessageView";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const encodings = [
  { label: "decoded", encode: (value) => value },
  { label: "JSON-encoded", encode: JSON.stringify },
];
const userMessage = { role: "user", content: "Find the weather" };
const view = (props) => render(createElement(ChatMessageView, props));

// Real Markdown, message cards, tool cards and theme providers are exercised.
// Only icon rendering is stubbed; no parsing or normalization is mocked.
describe("ChatMessageView recorded messages", () => {
  it.each(encodings)(
    "OBS-SW-27 renders every $label OTel output message in order",
    ({ encode }) => {
      const { container } = view({
        input: [
          {
            role: "user",
            parts: [{ type: "text", content: "Canonical input text" }],
          },
        ],
        output: encode([
          {
            role: "assistant",
            parts: [{ type: "text", content: "First canonical reply" }],
          },
          {
            role: "assistant",
            parts: [{ type: "text", content: "Second canonical reply" }],
          },
        ]),
      });

      expect(screen.getByText("Canonical input text")).toBeInTheDocument();
      expect(screen.getByText("First canonical reply")).toBeInTheDocument();
      expect(screen.getByText("Second canonical reply")).toBeInTheDocument();
      const text = container.textContent;
      expect(text.indexOf("Canonical input text")).toBeLessThan(
        text.indexOf("First canonical reply"),
      );
      expect(text.indexOf("First canonical reply")).toBeLessThan(
        text.indexOf("Second canonical reply"),
      );
      expect(text).not.toContain('"type":"text"');
    },
  );

  it("OBS-SW-27 renders OTel text parts in a singleton reply", () => {
    view({
      input: [userMessage],
      output: {
        role: "assistant",
        parts: [{ type: "text", content: "Singleton canonical reply" }],
      },
    });
    expect(screen.getByText("Singleton canonical reply")).toBeInTheDocument();
  });

  it("OBS-SW-27 retains every attribute-derived output message", () => {
    view({
      input: [userMessage],
      attrOutputMessages: [
        { role: "assistant", content: ["First attribute reply"] },
        { role: "assistant", content: ["Second attribute reply"] },
      ],
    });
    expect(screen.getByText("First attribute reply")).toBeInTheDocument();
    expect(screen.getByText("Second attribute reply")).toBeInTheDocument();
  });

  it("OBS-SW-27 retains repeated replies as distinct recorded messages", () => {
    view({
      input: [{ role: "assistant", content: "Repeated reply" }],
      output: [
        { role: "assistant", content: "Repeated reply" },
        { role: "assistant", content: "Repeated reply" },
      ],
    });
    expect(screen.getAllByText("Repeated reply")).toHaveLength(3);
  });

  it("OBS-SW-27 exposes all output messages through expand and collapse", () => {
    view({
      input: [userMessage],
      output: Array.from({ length: 5 }, (_, i) => ({
        role: "assistant",
        content: `Recorded reply ${i + 1}`,
      })),
    });
    expect(screen.queryByText("Recorded reply 1")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Show 2 more messages..." }),
    );
    for (let i = 1; i <= 5; i += 1) {
      expect(screen.getByText(`Recorded reply ${i}`)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: "Collapse messages" }));
    expect(screen.queryByText("Recorded reply 1")).not.toBeInTheDocument();
    expect(screen.getByText("Recorded reply 5")).toBeInTheDocument();
  });

  it.each(encodings)(
    "OBS-SW-28 detects $label request.messages histories",
    ({ encode }) => {
      expect(isOpenAIMessages(encode({ messages: [userMessage] }))).toBe(true);
    },
  );

  it.each(encodings)(
    "OBS-SW-28 prefers $label captured history and keeps tool details",
    ({ encode }) => {
      const captured = [
        userMessage,
        {
          role: "assistant",
          content: null,
          tool_calls: [
            {
              id: "call-weather-7",
              type: "function",
              function: {
                name: "lookup_weather",
                arguments: '{"city":"Oslo"}',
              },
            },
          ],
        },
        {
          role: "tool",
          content: "Weather result: clear skies",
          name: "weather_result",
          tool_call_id: "call-weather-7",
        },
      ];
      view({
        input: encode({ model: "test-model", messages: captured }),
        attrInputMessages: [
          { role: "user", content: ["Lossy attribute history"] },
          { role: "assistant", content: [""] },
          { role: "tool", content: ["Weather result: clear skies"] },
        ],
      });

      expect(screen.getByText("Find the weather")).toBeInTheDocument();
      expect(
        screen.queryByText("Lossy attribute history"),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByText("lookup_weather"));
      expect(screen.getByText('{"city":"Oslo"}')).toBeInTheDocument();
      expect(screen.getByText(": weather_result")).toBeInTheDocument();
      expect(screen.getByText("call-weather-7")).toBeInTheDocument();
      expect(
        screen.getByText("Weather result: clear skies"),
      ).toBeInTheDocument();
    },
  );

  it.each([
    { label: "string", body: { content: "Tool result" } },
    {
      label: "content array",
      body: { content: [{ type: "text", text: "Tool result" }] },
    },
    { label: "object", body: { content: { result: "Tool result" } } },
    { label: "parts", body: { parts: [{ text: "Tool result" }] } },
    { label: "null", body: { content: null } },
  ])(
    "OBS-SW-28 preserves tool-result metadata for $label content",
    ({ body }) => {
      view({
        input: [
          {
            role: "tool",
            name: "recorded_result",
            tool_call_id: "call-result-3",
            ...body,
          },
        ],
      });
      expect(screen.getByText(": recorded_result")).toBeInTheDocument();
      expect(screen.getByText("call-result-3")).toBeInTheDocument();
    },
  );

  it("OBS-SW-28 preserves tool calls alongside nonempty assistant content", () => {
    view({
      input: [userMessage],
      output: {
        role: "assistant",
        content: "Looking it up",
        tool_calls: [
          {
            id: "call-lookup-9",
            function: { name: "lookup_city", arguments: '{"city":"Bergen"}' },
          },
        ],
      },
    });
    expect(screen.getByText("Looking it up")).toBeInTheDocument();
    fireEvent.click(screen.getByText("lookup_city"));
    expect(screen.getByText('{"city":"Bergen"}')).toBeInTheDocument();
  });

  it("OBS-SW-28 preserves supported camel-case tool metadata", () => {
    view({
      input: [
        {
          role: "assistant",
          content: "Calling the tool",
          toolCalls: [{ name: "camel_lookup", arguments: "camel arguments" }],
        },
        {
          role: "tool",
          content: "Done",
          name: "camel_result",
          toolCallId: "camel-id",
        },
      ],
    });
    fireEvent.click(screen.getByText("camel_lookup"));
    expect(screen.getByText("camel arguments")).toBeInTheDocument();
    expect(screen.getByText(": camel_result")).toBeInTheDocument();
    expect(screen.getByText("camel-id")).toBeInTheDocument();
  });

  it.each([
    { label: "missing", input: undefined },
    { label: "unparseable", input: "not JSON" },
    { label: "JSON null", input: "null" },
    { label: "empty request", input: { messages: [] } },
  ])("falls back to attributes for $label captured history", ({ input }) => {
    view({
      input,
      attrInputMessages: [{ role: "user", content: ["Attribute fallback"] }],
    });
    expect(screen.getByText("Attribute fallback")).toBeInTheDocument();
  });

  it.each([
    { label: "direct OpenAI", input: [userMessage] },
    {
      label: "Gemini",
      input: {
        contents: [{ role: "user", parts: [{ text: "Find the weather" }] }],
      },
    },
  ])("retains $label input detection and rendering", ({ input }) => {
    expect(isOpenAIMessages(input)).toBe(true);
    view({ input });
    expect(screen.getByText("Find the weather")).toBeInTheDocument();
  });

  it.each([
    { label: "plain text", output: "Legacy reply" },
    {
      label: "single message",
      output: { role: "assistant", content: "Legacy reply" },
    },
    {
      label: "JSON single message",
      output: JSON.stringify({ role: "assistant", content: "Legacy reply" }),
    },
    { label: "content object", output: { content: "Legacy reply" } },
    {
      label: "Gemini",
      output: { content: { role: "model", parts: [{ text: "Legacy reply" }] } },
    },
  ])("retains the $label output fallback", ({ output }) => {
    view({ input: [userMessage], output });
    expect(screen.getByText("Legacy reply")).toBeInTheDocument();
  });

  it("retains attribute output precedence over an unstructured raw reply", () => {
    view({
      input: [userMessage],
      output: "Raw reply",
      attrOutputMessages: [{ role: "assistant", content: "Attribute reply" }],
    });
    expect(screen.getByText("Attribute reply")).toBeInTheDocument();
    expect(screen.queryByText("Raw reply")).not.toBeInTheDocument();
  });
});

const langchainMessage = (className, kwargs) => ({
  lc: 1,
  type: "constructor",
  id: ["langchain_core", "messages", className],
  kwargs,
});
// fi-collector openllmetry_content_test.go: the input keeps a tool_calls list
// while the output keeps OpenLLMetry's flat tool_calls.N.* keys.
const openllmetryInput = [
  { role: "user", content: [{ type: "text", text: "question" }] },
  {
    role: "assistant",
    content: "",
    tool_calls: [
      {
        id: "call_example",
        function: { name: "lookup", arguments: '{"query":"example"}' },
      },
    ],
  },
];
const openllmetryOutput = [
  {
    role: "assistant",
    content: "",
    "tool_calls.0.id": "call_next",
    "tool_calls.0.function.name": "lookup",
    "tool_calls.0.function.arguments": '{"query":"next"}',
    parallel_tool_calls: false,
    index: 0,
  },
  { role: "assistant", content: [{ type: "text", text: "answer" }] },
];
// The OTel GenAI parts shape pinned by insights/tests/test_evidence.py.
const otelInput = [
  { role: "user", parts: [{ type: "text", content: "What is this?" }] },
  {
    role: "assistant",
    parts: [
      {
        type: "tool_call",
        id: "c1",
        name: "lookup",
        arguments: { q: "logo" },
      },
    ],
  },
  {
    role: "tool",
    parts: [
      { type: "tool_call_response", id: "c1", response: { brand: "Acme" } },
    ],
  },
];
const otelOutput = [
  {
    role: "assistant",
    parts: [
      { type: "reasoning", content: "Logo check." },
      { type: "text", content: "An Acme logo." },
    ],
  },
];

describe("ChatMessageView framework tool turns", () => {
  it("OBS-SW-137 keeps LangChain tool calls, tool results and class-name roles", () => {
    const { container } = view({
      input: {
        messages: [
          [
            langchainMessage("HumanMessage", { content: "Add two and two" }),
            langchainMessage("AIMessage", {
              content: "",
              tool_calls: [
                {
                  name: "add_numbers",
                  args: { a: 2, b: 2 },
                  id: "call-add-1",
                  type: "tool_call",
                },
              ],
            }),
            langchainMessage("ToolMessage", {
              content: "Sum is four",
              tool_call_id: "call-add-1",
            }),
          ],
        ],
      },
    });

    expect(screen.getByText("user")).toBeInTheDocument();
    expect(screen.getByText("Add two and two")).toBeInTheDocument();
    fireEvent.click(screen.getByText("add_numbers"));
    const text = container.textContent;
    expect(text).toContain('"a": 2');
    expect(screen.getByText("call-add-1")).toBeInTheDocument();
    expect(screen.getByText("Sum is four")).toBeInTheDocument();
    expect(text).not.toContain('"lc"');
    expect(text).not.toContain("(empty)");
  });

  it("OBS-SW-137 falls back to attribute turns when no captured turn is readable", () => {
    view({
      input: { messages: [{ text: "Opaque record" }] },
      attrInputMessages: [
        { role: "user", content: ["Readable attribute turn"] },
      ],
    });
    expect(screen.getByText("Readable attribute turn")).toBeInTheDocument();
    expect(screen.queryByText("unknown")).not.toBeInTheDocument();
  });

  it.each(encodings)(
    "OBS-SW-138 shows $label OpenLLMetry flat tool_calls keys as tool cards",
    ({ encode }) => {
      const { container } = view({
        input: encode(openllmetryInput),
        output: encode(openllmetryOutput),
      });

      const toolCards = screen.getAllByText("lookup");
      expect(toolCards).toHaveLength(2);
      fireEvent.click(toolCards[1]);
      expect(screen.getByText('{"query":"next"}')).toBeInTheDocument();
      expect(screen.getByText("answer")).toBeInTheDocument();
      expect(container.textContent).not.toContain("(empty)");
      expect(container.textContent).not.toContain("tool_calls.0");
    },
  );

  it("OBS-SW-138 shows Traceloop tool_calls.N.name keys in a reply and in its history", () => {
    const toolTurn = {
      role: "assistant",
      content: "",
      finish_reason: "tool_calls",
      "tool_calls.0.id": "call_1",
      "tool_calls.0.name": "get_weather",
      "tool_calls.0.arguments": '{"city":"Paris"}',
    };
    const { container } = view({
      input: [{ role: "user", content: "Weather in Paris?" }, toolTurn],
      output: toolTurn,
    });

    const toolCards = screen.getAllByText("get_weather");
    expect(toolCards).toHaveLength(2);
    fireEvent.click(toolCards[1]);
    expect(screen.getAllByText('{"city":"Paris"}')).toHaveLength(2);
    expect(container.textContent).not.toContain("(empty)");
  });

  it.each(encodings)(
    "OBS-SW-139 shows $label OTel GenAI tool_call, tool_call_response and reasoning parts",
    ({ encode }) => {
      const { container } = view({
        input: encode(otelInput),
        output: encode(otelOutput),
      });

      fireEvent.click(screen.getByText("lookup"));
      const text = container.textContent;
      expect(text).toContain('"q": "logo"');
      expect(screen.getByText("c1")).toBeInTheDocument();
      expect(text).toContain('"brand": "Acme"');
      expect(screen.getByText("Logo check.")).toBeInTheDocument();
      expect(screen.getByText("An Acme logo.")).toBeInTheDocument();
      expect(text).not.toContain('"type":"tool_call');
      expect(text).not.toContain('"type":"reasoning"');
      expect(text).not.toContain("(empty)");
    },
  );
});
