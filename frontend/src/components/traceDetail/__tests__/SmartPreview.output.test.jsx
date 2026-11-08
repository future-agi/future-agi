import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";

const input = JSON.stringify([
  { role: "system", content: "You are a coding assistant." },
  { role: "user", content: "Fix the median for an even-length list." },
]);
const reply = "The median now averages the two middle values.";
const toolBlock = {
  type: "tool_use",
  id: "tool-call-fixture",
  name: "read_file",
  input: { path: "stats.py" },
};
const EmptyCard = () => null;
const JsonPreview = ({ input: rawInput, output }) => (
  <pre data-testid="json-preview">
    {JSON.stringify({ input: rawInput, output })}
  </pre>
);
JsonPreview.propTypes = { input: PropTypes.any, output: PropTypes.any };

function preview(output, viewMode, attributes = {}, rawInput = input) {
  return (
    <SmartPreview
      span={{ id: "span-fixture", observation_type: "LLM" }}
      input={rawInput}
      output={output}
      attributes={attributes}
      viewMode={viewMode}
      ContentCard={EmptyCard}
      AttributesCard={EmptyCard}
      JsonPreviewBlock={JsonPreview}
    />
  );
}

// The trace API exposes input.value/output.value as flat input/output fields.
const replies = [
  ["JSON content-block array", JSON.stringify([{ type: "text", text: reply }])],
  ["decoded content-block array", [{ type: "text", text: reply }]],
  ["plain text", reply],
  ["JSON string", JSON.stringify(reply)],
  ["OpenAI message", JSON.stringify({ role: "assistant", content: reply })],
  [
    "Gemini message",
    JSON.stringify({ content: { role: "model", parts: [{ text: reply }] } }),
  ],
];

describe.each(["chat", "markdown"])(
  "unit: SmartPreview %s conversation output",
  (mode) => {
    it.each(replies)(
      "renders %s after the input messages",
      (_shape, output) => {
        render(preview(output, mode));

        const prompt = screen.getByText(
          "Fix the median for an even-length list.",
        );
        const answer = screen.getByText(reply);
        expect(screen.getAllByText(reply)).toHaveLength(1);
        expect(
          prompt.compareDocumentPosition(answer) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy();
        expect(screen.getByText(/^(assistant|model)$/)).toBeVisible();
      },
    );

    it.each([
      ["content-block array", JSON.stringify([toolBlock])],
      [
        "OpenAI tool call",
        JSON.stringify({
          role: "assistant",
          content: null,
          tool_calls: [
            {
              id: "tool-call-fixture",
              type: "function",
              function: {
                name: "read_file",
                arguments: JSON.stringify({ path: "stats.py" }),
              },
            },
          ],
        }),
      ],
    ])("keeps tool-call-only %s output visible", (_shape, output) => {
      render(preview(output, mode));

      expect(screen.getByText("assistant")).toBeVisible();
      expect(screen.getByText(/read_file/)).toBeVisible();
      expect(screen.getByText(/stats\.py/)).toBeVisible();
    });

    it("preserves text and tool blocks together", () => {
      render(
        preview(
          JSON.stringify([{ type: "text", text: reply }, toolBlock]),
          mode,
        ),
      );
      expect(screen.getByText(reply)).toBeVisible();
      expect(screen.getByText(/read_file/)).toBeVisible();
    });

    it.each([undefined, null, "", "null", []])(
      "handles absent output %j",
      (output) => {
        render(preview(output, mode));
        expect(
          screen.getByText("Fix the median for an even-length list."),
        ).toBeVisible();
        expect(screen.queryByText("assistant")).not.toBeInTheDocument();
      },
    );

    it.each([
      0,
      false,
      "0",
      "false",
      '{"unexpected":"preserve this reply"}',
      '{"truncated":',
    ])("does not discard unrecognized output %s", (output) => {
      render(preview(output, mode));
      expect(screen.getByText("assistant")).toBeVisible();
      expect(screen.getByText(String(output))).toBeVisible();
    });

    it("prefers attribute-extracted replies without repeating raw output", () => {
      render(
        preview(
          JSON.stringify([{ type: "text", text: "raw fallback" }]),
          mode,
          {
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": reply,
          },
        ),
      );
      expect(screen.getAllByText(reply)).toHaveLength(1);
      expect(screen.queryByText("raw fallback")).not.toBeInTheDocument();
    });

    it("keeps the final reply visible in collapsed conversations and after expansion", () => {
      const history = JSON.stringify(
        Array.from({ length: 12 }, (_, i) => ({
          role: i % 2 ? "assistant" : "user",
          content: `Previous message ${i}`,
        })),
      );
      render(
        preview(
          JSON.stringify([{ type: "text", text: reply }]),
          mode,
          {},
          history,
        ),
      );
      expect(screen.getByText(reply)).toBeVisible();
      fireEvent.click(
        screen.getByRole("button", { name: /Show .* more messages/ }),
      );
      expect(screen.getAllByText(reply)).toHaveLength(1);
      expect(screen.getByText("Previous message 5")).toBeVisible();
    });
  },
);

it("unit: leaves JSON preview input and output unchanged", () => {
  const output = JSON.stringify([{ type: "text", text: reply }, toolBlock]);
  render(preview(output, "json"));
  expect(JSON.parse(screen.getByTestId("json-preview").textContent)).toEqual({
    input,
    output,
  });
});
