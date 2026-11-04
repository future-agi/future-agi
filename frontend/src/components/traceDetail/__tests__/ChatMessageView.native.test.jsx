import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import ChatMessageView, { isOpenAIMessages } from "../ChatMessageView";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({
  palette: {
    accent: {
      neutral: "#666666",
      info: "#0066cc",
      agent: "#008844",
      tool: "#885500",
      pass: "#008844",
    },
  },
});
const message = (role, content) => ({
  role,
  parts: [{ type: "text", content }],
});
const renderChat = (props) =>
  render(
    <ThemeProvider theme={theme}>
      <ChatMessageView {...props} />
    </ThemeProvider>,
  );

describe("native GenAI chat preview", () => {
  it("renders all native response messages and text parts without raw JSON", () => {
    renderChat({
      input: JSON.stringify([
        message("system", "SYSTEM"),
        message("user", "PROMPT"),
      ]),
      output: JSON.stringify([
        message("assistant", "FIRST"),
        message("assistant", "SECOND"),
      ]),
    });
    for (const text of ["SYSTEM", "PROMPT", "FIRST", "SECOND"])
      expect(screen.getByText(text, { exact: true })).toBeInTheDocument();
  });

  it("renders all attribute response messages rather than only the first", () => {
    renderChat({
      attrInputMessages: [{ role: "user", content: "PROMPT" }],
      attrOutputMessages: [
        message("assistant", "FIRST"),
        message("assistant", "SECOND"),
      ],
    });
    expect(screen.getByText("FIRST", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("SECOND", { exact: true })).toBeInTheDocument();
  });

  it.each(["null", "[null]", "[]", "false"])(
    "handles malformed message shape %s",
    (input) => {
      expect(isOpenAIMessages(input)).toBe(false);
      expect(() => renderChat({ input, output: "null" })).not.toThrow();
    },
  );

  it("preserves legacy OpenAI and Gemini text previews", () => {
    renderChat({
      input: [{ role: "user", content: "LEGACY" }],
      output: { content: { role: "model", parts: [{ text: "GEMINI" }] } },
    });
    expect(screen.getByText("LEGACY", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("GEMINI", { exact: true })).toBeInTheDocument();
  });
});
