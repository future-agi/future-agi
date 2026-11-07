import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import SmartPreview from "../SmartPreview";
import ChatMessageView, { isOpenAIMessages } from "../ChatMessageView";
import captures from "./fixtures/native-anthropic.json";

// Real SDK 12.56.0 / 13.3.0 content-0.pb captures, converted by the collector.
// The trace detail reader JSON-decodes the input/output columns, not their parts.
// Keep SmartPreview, ChatMessageView, getLlmData and Markdown real; only icons
// are stubbed so icon discovery cannot make outbound requests.
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
const renderPreview = (
  { input, output, attributes = {} },
  viewMode = "markdown",
) =>
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
const renderChat = (props) =>
  render(
    <ThemeProvider theme={theme}>
      <ChatMessageView {...props} />
    </ThemeProvider>,
  );
const native = (text, role = "assistant") => ({
  role,
  parts: [{ type: "text", content: text }],
});

afterEach(cleanup);

describe.each(captures)(
  "native Anthropic $sdkVersion active preview",
  (capture) => {
    it.each(["markdown", "chat"])(
      "shows input and response in %s mode",
      (mode) => {
        renderPreview(capture, mode);
        for (const text of [
          "SYSTEM_SENTINEL",
          "PROMPT_SENTINEL",
          "RESPONSE_SENTINEL",
        ]) {
          expect(screen.getByText(text, { selector: "p" })).toBeVisible();
        }
      },
    );
    it("also renders the serialized-column response", () => {
      renderPreview({ ...capture, output: JSON.stringify(capture.output) });
      expect(
        screen.getByText("RESPONSE_SENTINEL", { selector: "p" }),
      ).toBeVisible();
    });
  },
);

describe("native message variants and existing formats", () => {
  it("renders every native response message in order, not just the first", () => {
    renderChat({ output: [native("First"), native("Second")] });
    expect(
      screen.getAllByText(/^(First|Second)$/).map((p) => p.textContent),
    ).toEqual(["First", "Second"]);
  });
  it("normalizes attribute-extracted native responses too", () => {
    renderChat({ attrOutputMessages: [native("First"), native("Second")] });
    expect(screen.getByText("First", { selector: "p" })).toBeVisible();
    expect(screen.getByText("Second", { selector: "p" })).toBeVisible();
  });
  it("recognizes output-only native observations in the active preview", () => {
    renderPreview({ output: [native("Response only")] });
    expect(screen.getByText("Response only", { selector: "p" })).toBeVisible();
  });
  it.each([
    ["Plain prompt", "Plain prompt"],
    [{ question: "Plain prompt" }, '{"question":"Plain prompt"}'],
    [false, "false"],
    [0, "0"],
  ])(
    "keeps non-message input %j visible beside a native response",
    (input, text) => {
      renderPreview({ input, output: [native("Native response")] });
      expect(screen.getByText(text, { selector: "p" })).toBeVisible();
      expect(
        screen.getByText("Native response", { selector: "p" }),
      ).toBeVisible();
    },
  );
  it("uses unindexed native attributes when hot columns are absent", () => {
    renderPreview({
      attributes: {
        "gen_ai.output.messages": JSON.stringify([
          native("Attribute response"),
        ]),
      },
    });
    expect(
      screen.getByText("Attribute response", { selector: "p" }),
    ).toBeVisible();
  });
  it.each([null, "", [], "[redacted]", false, 0])(
    "does not resurrect native content behind explicit %j output",
    (output) => {
      renderPreview({
        input: [{ role: "user", content: "Safe input" }],
        output,
        attributes: {
          "gen_ai.output.messages": JSON.stringify([
            native("PRIVATE_SENTINEL"),
          ]),
        },
      });
      expect(screen.queryByText(/PRIVATE_SENTINEL/)).toBeNull();
    },
  );
  it("preserves non-text native parts rather than dropping tool data", () => {
    const { container } = renderChat({
      output: [
        {
          role: "assistant",
          parts: [
            { type: "text", content: "Tool response" },
            {
              type: "tool_call",
              id: "tool-1",
              name: "weather",
              arguments: { city: "Oslo", enabled: false, count: 0 },
            },
          ],
        },
      ],
    });
    expect(screen.getByText("Tool response", { selector: "p" })).toBeVisible();
    expect(container.textContent).toContain('"city":"Oslo"');
    expect(container.textContent).toContain('"enabled":false');
  });
  it.each([
    { role: "assistant", content: "Canonical response" },
    { content: { role: "model", parts: [{ text: "Canonical response" }] } },
    "Canonical response",
  ])("keeps canonical and Gemini responses visible: %j", (output) => {
    renderChat({ output });
    expect(
      screen.getByText("Canonical response", { selector: "p" }),
    ).toBeVisible();
  });
  it.each([null, "null", [], [null], "[null]", "{bad json"])(
    "safely rejects non-message input %j",
    (input) => {
      expect(isOpenAIMessages(input)).toBe(false);
    },
  );
});
