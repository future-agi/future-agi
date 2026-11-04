import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import SmartPreview from "../SmartPreview";

// Exercise the active preview and real message renderer, not a mocked chat card.
vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({
  palette: {
    accent: {
      neutral: "#777777",
      info: "#3366aa",
      agent: "#9933aa",
      tool: "#bb7722",
      pass: "#228833",
    },
  },
});
const Empty = () => null;
const input = [{ role: "user", content: "Return records as JSON" }];
const renderPreview = (output, viewMode, attributes = {}) =>
  render(
    <ThemeProvider theme={theme}>
      <SmartPreview
        span={{ observation_type: "llm" }}
        input={input}
        output={output}
        attributes={attributes}
        viewMode={viewMode}
        ContentCard={Empty}
        AttributesCard={Empty}
        JsonPreviewBlock={Empty}
      />
    </ThemeProvider>,
  );

afterEach(cleanup);

const structuredOutputs = [
  ...[42, { id: 42 }, true, false, null].map((role) => [
    { role, content: "SYNTHETIC_RECORD", extra: { retained: true } },
  ]),
  [
    { role: "assistant", content: "First record" },
    { role: 42, content: "SYNTHETIC_RECORD" },
  ],
  [{ role: "employee", id: 42, label: "SYNTHETIC_RECORD" }],
  [
    {
      role: "employee",
      content: "SYNTHETIC_EMPLOYEE_RECORD",
      name: { first: "Ada", last: "Lovelace" },
      extra: { retained: true },
    },
  ],
  [{ role: "employee", content: "SYNTHETIC_RECORD", name: "Ada" }],
  ...[
    { name: { first: "Ada", last: "Lovelace" } },
    { name: ["Ada"] },
    { tool_call_id: { id: "call_1" } },
    { toolCallId: { id: "call_1" } },
    { tool_calls: "not-an-array" },
    { toolCalls: { length: 1 } },
    { tool_calls: [null] },
    { toolCalls: ["not-a-tool"] },
    { tool_calls: [{ function: { name: { first: "Ada" } } }] },
    { toolCalls: [{ name: { first: "Ada" } }] },
    { tool_calls: [{ function: "not-a-function" }] },
    { tool_calls: [{ id: { id: "call_1" }, function: { name: "lookup" } }] },
  ].map((metadata) => [
    { role: "assistant", content: "SYNTHETIC_RECORD", ...metadata },
  ]),
  [
    {
      role: "assistant",
      content: { employee: "Ada" },
      extra: { retained: true },
    },
  ],
  [{ role: "assistant", content: 42, extra: { retained: true } }],
  [
    {
      role: "assistant",
      parts: [{ type: "text", content: "SYNTHETIC_RECORD" }],
      name: { first: "Ada" },
    },
  ],
  [
    { role: "assistant", content: "First record" },
    { role: "assistant", content: "SYNTHETIC_RECORD", name: { first: "Ada" } },
  ],
  [{ content: "SYNTHETIC_RECORD", id: 42 }],
  ["SYNTHETIC_RECORD", 42, false, null],
  [[{ role: "user", content: "SYNTHETIC_RECORD" }]],
  [],
];

describe.each(["markdown", "chat"])(
  "structured output in %s preview",
  (mode) => {
    describe.each([false, true])("serialized column: %s", (serialized) => {
      it.each(structuredOutputs.map((output) => [output]))(
        "renders non-message array %j as lossless JSON",
        (output) => {
          let rendered;
          expect(() => {
            rendered = renderPreview(
              serialized ? JSON.stringify(output) : output,
              mode,
            );
          }).not.toThrow();
          expect(screen.getByText("Return records as JSON")).toBeVisible();
          const json = rendered.container.querySelector("pre code");
          expect(json).toBeVisible();
          expect(JSON.parse(json.textContent)).toEqual(output);
        },
      );
    });

    it.each([42, { id: 42 }, true])(
      "normalizes a malformed attribute role %j without crashing",
      (role) => {
        expect(() =>
          renderPreview(undefined, mode, {
            "llm.output_messages.0.message.role": role,
            "llm.output_messages.0.message.content": "ATTRIBUTE_RESPONSE",
          }),
        ).not.toThrow();
        expect(screen.getByText("ATTRIBUTE_RESPONSE")).toBeVisible();
        expect(screen.getByText("unknown")).toBeVisible();
      },
    );

    it.each([false, true])(
      "keeps real tool-message metadata (serialized: %s)",
      (serialized) => {
        const output = [
          {
            role: "assistant",
            content: null,
            name: "synthetic-assistant",
            tool_calls: [
              {
                id: "call_1",
                type: "function",
                function: { name: "lookup_employee", arguments: '{"id":42}' },
              },
            ],
          },
          {
            role: "tool",
            content: "TOOL_RESPONSE",
            name: "lookup_employee",
            tool_call_id: "call_1",
          },
          {
            role: "assistant",
            content: "FINAL_RESPONSE",
            toolCalls: [
              { id: "call_2", name: "lookup_department", arguments: { id: 7 } },
            ],
            toolCallId: "call_2",
          },
        ];
        const { container } = renderPreview(
          serialized ? JSON.stringify(output) : output,
          mode,
        );
        for (const text of [
          ": synthetic-assistant",
          "lookup_department",
          "TOOL_RESPONSE",
          "FINAL_RESPONSE",
          "call_1",
          "call_2",
        ])
          expect(screen.getByText(text, { exact: true })).toBeVisible();
        expect(container.querySelector("pre code")).toBeNull();
      },
    );

    it("keeps all real OpenAI, Gemini and native response messages", () => {
      const { container } = renderPreview(
        [
          { role: "assistant", content: "First answer" },
          { role: "model", parts: [{ text: "Second answer" }] },
          {
            role: "assistant",
            parts: [{ type: "text", content: "Native answer" }],
          },
        ],
        mode,
      );
      expect(screen.getByText("First answer")).toBeVisible();
      expect(screen.getByText("Second answer")).toBeVisible();
      expect(screen.getByText("Native answer")).toBeVisible();
      expect(container.querySelector("pre code")).toBeNull();
    });
  },
);
