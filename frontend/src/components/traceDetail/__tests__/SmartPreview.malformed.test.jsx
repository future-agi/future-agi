import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";
import { palette } from "src/theme/palette";

const EmptyCard = () => null;
const prompt = "Malformed telemetry must not hide the conversation.";
const input = JSON.stringify([{ role: "user", content: prompt }]);
const roles = [
  "constructor",
  "__proto__",
  "toString",
  "hasOwnProperty",
  42,
  true,
  {},
  null,
];
const nestedText = [
  ["non-callable toString", { toString: 42 }, "toString"],
  ["non-callable valueOf", { valueOf: {} }, "valueOf"],
  ["non-callable toJSON", { toJSON: 42 }, "toJSON"],
  [
    "deep nested text",
    { text: { toString: 42 }, nested: { child: { leaf: "deep reply" } } },
    "deep reply",
  ],
  ["mixed primitive array", ["mixed reply", 42, true, null], "mixed reply"],
];
const cases = roles.flatMap((role) =>
  nestedText.map(([label, value, expected]) => ({
    label: `${JSON.stringify(role)} / ${label}`,
    role,
    value,
    expected,
  })),
);

function preview(mode, source, role, value) {
  const block = { text: value };
  const attributes =
    source === "attributes"
      ? {
          "llm.output_messages.0.message.role": "assistant",
          "llm.output_messages.0.message.content": "First normal reply",
          "llm.output_messages.1.message.role": role,
          "llm.output_messages.1.message.content": [block],
        }
      : {};
  const output = source === "raw" ? [{ role, content: [block] }] : undefined;
  return (
    <SmartPreview
      span={{ id: "malformed-fixture", observation_type: "LLM" }}
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
  "unit: SmartPreview %s malformed matrix",
  (mode) => {
    describe.each(["attributes", "raw"])("%s", (source) => {
      // Dev ignores later replies; safety controls therefore assert the existing
      // conversation stays visible. Retention assertions below additionally pin
      // the new behavior rather than mistaking dev's omission for a complete fix.
      it.each(cases)(
        "control: $label cannot crash the conversation",
        ({ role, value }) => {
          expect(() =>
            render(preview(mode, source, role, value)),
          ).not.toThrow();
          expect(screen.getByText(prompt)).toBeVisible();
          if (source === "attributes")
            expect(screen.getByText("First normal reply")).toBeVisible();
        },
      );
      it.each(cases)(
        "retention: $label keeps readable output",
        ({ role, value, expected }) => {
          const { container } = render(preview(mode, source, role, value));
          expect(container).toHaveTextContent(expected);
          if (typeof role === "string")
            expect(screen.getByText(role)).toBeVisible();
        },
      );
    });

    it.each(["constructor", "__proto__", "toString", "hasOwnProperty"])(
      "uses the fallback badge style and preserves the %s label",
      (role) => {
        render(preview(mode, "raw", role, "Visible custom reply"));
        expect(screen.getByText(role)).toHaveStyle({
          color: palette("light").accent.info,
        });
        expect(screen.getByText("Visible custom reply")).toBeVisible();
      },
    );

    describe.each(["attributes", "raw"])(
      "%s non-JSON decoded values",
      (source) => {
        const cyclic = { marker: "cycle reply" };
        cyclic.self = cyclic;
        const throwing = () => {
          throw new Error("coercion must not execute");
        };
        it.each([
          ["circular", cyclic, "Circular"],
          ["bigint", { count: 123n }, "123"],
          ["symbol", { token: Symbol("reply-symbol") }, "reply-symbol"],
          ["function", { callback: throwing }, "Function"],
          ["throwing toString", { toString: throwing }, "toString"],
          ["throwing valueOf", { valueOf: throwing }, "valueOf"],
          ["throwing toJSON", { toJSON: throwing }, "toJSON"],
          [
            "throwing accessor",
            Object.defineProperty({}, "text", {
              enumerable: true,
              get: throwing,
            }),
            "Accessor",
          ],
        ])("retention: safely serializes %s", (_label, value, expected) => {
          const { container } = render(
            preview(mode, source, "assistant", value),
          );
          expect(screen.getByText(prompt)).toBeVisible();
          expect(container).toHaveTextContent(expected);
        });
      },
    );

    it.each(["text", "content", "delta"])(
      "preserves non-string %s blocks as JSON",
      (field) => {
        const output = JSON.stringify([
          { [field]: { text: { toString: 42 } } },
        ]);
        const { container } = render(
          <SmartPreview
            span={{ observation_type: "LLM" }}
            input={input}
            output={output}
            viewMode={mode}
            ContentCard={EmptyCard}
            AttributesCard={EmptyCard}
            JsonPreviewBlock={EmptyCard}
          />,
        );
        expect(container).toHaveTextContent(field);
        expect(container).toHaveTextContent("toString");
      },
    );
  },
);
