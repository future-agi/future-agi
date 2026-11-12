import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";

const EmptyCard = () => null;
const input = JSON.stringify([
  { role: "user", content: "Independent review prompt" },
]);
function view(mode, attributes = {}, output) {
  return (
    <SmartPreview
      span={{ id: "review-fixture", observation_type: "LLM" }}
      input={input}
      output={output}
      attributes={attributes}
      viewMode={mode}
      ContentCard={EmptyCard}
      AttributesCard={EmptyCard}
      JsonPreviewBlock={EmptyCard}
    />
  );
}
describe.each(["chat", "markdown"])(
  "unit: independent %s telemetry robustness",
  (mode) => {
    it.each(["__proto__", "constructor", "toString", "prototype"])(
      "later attribute role %s cannot crash",
      (role) => {
        const attributes = {
          "llm.output_messages.0.message.role": "assistant",
          "llm.output_messages.0.message.content": "First normal reply",
          "llm.output_messages.1.message.role": role,
          "llm.output_messages.1.message.content": "Second unusual reply",
        };
        expect(() => render(view(mode, attributes))).not.toThrow();
        expect(screen.getByText("Independent review prompt")).toBeVisible();
      },
    );
    it.each(["__proto__", "constructor"])(
      "raw message array role %s cannot crash",
      (role) => {
        const output = JSON.stringify([{ role, content: "Unusual raw reply" }]);
        expect(() => render(view(mode, {}, output))).not.toThrow();
        expect(screen.getByText("Independent review prompt")).toBeVisible();
      },
    );
    it.each([
      ["object text", [{ text: { notText: true } }]],
      [
        "nested text with non-callable toString",
        [{ text: { text: { toString: 42 } } }],
      ],
      ["numeric text", [{ type: "text", text: 42 }]],
      ["null and scalars", [null, 0, false, { foo: "bar" }]],
    ])("raw blocks with %s cannot crash", (_label, output) => {
      expect(() =>
        render(view(mode, {}, JSON.stringify(output))),
      ).not.toThrow();
      expect(screen.getByText("Independent review prompt")).toBeVisible();
    });
  },
);
