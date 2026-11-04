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
  [{ content: "SYNTHETIC_RECORD", id: 42 }],
  ["SYNTHETIC_RECORD", 42, false, null],
  [[{ role: "user", content: "SYNTHETIC_RECORD" }]],
  [],
];

describe.each(["markdown", "chat"])("structured output in %s preview", (mode) => {
  describe.each([false, true])("serialized column: %s", (serialized) => {
    it.each(structuredOutputs.map((output) => [output]))(
      "renders non-message array %j as lossless JSON",
      (output) => {
        let rendered;
        expect(() => {
          rendered = renderPreview(serialized ? JSON.stringify(output) : output, mode);
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

  it("keeps all real OpenAI, Gemini and native response messages", () => {
    const { container } = renderPreview(
      [
        { role: "assistant", content: "First answer" },
        { role: "model", parts: [{ text: "Second answer" }] },
        { role: "assistant", parts: [{ type: "text", content: "Native answer" }] },
      ],
      mode,
    );
    expect(screen.getByText("First answer")).toBeVisible();
    expect(screen.getByText("Second answer")).toBeVisible();
    expect(screen.getByText("Native answer")).toBeVisible();
    expect(container.querySelector("pre code")).toBeNull();
  });
});
