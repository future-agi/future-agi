import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SmartPreview from "../SmartPreview";

const prompt = "Explain the role guard.";
const firstReply = "First reply";
const secondReply = "Second reply";
const input = JSON.stringify([{ role: "user", content: prompt }]);
const EmptyCard = () => null;
const malformedRoles = [
  ["numeric", 42],
  ["boolean", true],
  ["object", { malformed: true }],
  ["zero", 0],
  ["false", false],
  ["null", null],
];

function preview(mode, attributes = {}, rawInput = input) {
  return (
    <SmartPreview
      span={{ id: "role-fixture", observation_type: "LLM" }}
      input={rawInput}
      attributes={attributes}
      viewMode={mode}
      ContentCard={EmptyCard}
      AttributesCard={EmptyCard}
      JsonPreviewBlock={EmptyCard}
    />
  );
}

function outputAttributes(role) {
  return {
    "llm.output_messages.0.message.role": "assistant",
    "llm.output_messages.0.message.content": firstReply,
    "llm.output_messages.1.message.role": role,
    "llm.output_messages.1.message.content": secondReply,
  };
}

describe.each(["chat", "markdown"])(
  "unit: SmartPreview %s malformed message roles",
  (mode) => {
    // These controls also pass on dev, which ignores later attribute replies.
    it.each(malformedRoles)(
      "does not crash when a later attribute reply has a %s role",
      (_kind, role) => {
        expect(() => render(preview(mode, outputAttributes(role)))).not.toThrow();
        expect(screen.getByText(prompt)).toBeVisible();
        expect(screen.getByText(firstReply)).toBeVisible();
      },
    );

    it.each(malformedRoles)(
      "retains later attribute reply with %s role",
      (_kind, role) => {
        render(preview(mode, outputAttributes(role)));
        const first = screen.getByText(firstReply);
        const second = screen.getByText(secondReply);
        expect(first).toBeVisible();
        expect(second).toBeVisible();
        expect(screen.getAllByText(secondReply)).toHaveLength(1);
        expect(screen.getByText("unknown")).toBeVisible();
        expect(
          first.compareDocumentPosition(second) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy();
      },
    );

    it.each(malformedRoles)(
      "normalizes a %s role in raw input messages",
      (_kind, role) => {
        render(preview(mode, {}, JSON.stringify([{ role, content: prompt }])));
        expect(screen.getByText(prompt)).toBeVisible();
        expect(screen.getByText("unknown")).toBeVisible();
      },
    );

    it.each(malformedRoles)(
      "keeps attribute input content with a %s role",
      (_kind, role) => {
        render(
          preview(mode, {
            "llm.input_messages.0.message.role": role,
            "llm.input_messages.0.message.content": "Attribute prompt",
          }),
        );
        expect(screen.getByText("Attribute prompt")).toBeVisible();
        expect(screen.getByText("unknown")).toBeVisible();
        expect(screen.queryByText(prompt)).not.toBeInTheDocument();
      },
    );

    it.each(["Assistant", "model", "developer", "custom-provider-role"])(
      "preserves the valid role string %s",
      (role) => {
        render(preview(mode, outputAttributes(role)));
        expect(screen.getByText(role)).toBeVisible();
        expect(screen.getByText(secondReply)).toBeVisible();
        expect(screen.queryByText("unknown")).not.toBeInTheDocument();
      },
    );
  },
);
