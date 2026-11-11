import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import LLMSpan from "../RightSpans/LLMSpan";

describe("unit: legacy LLM span role labels", () => {
  it.each([42, true, { malformed: true }, 0, false, null])(
    "keeps message content visible for malformed role %j",
    (role) => {
      render(
        <LLMSpan
          column={{ headerName: "Output" }}
          tabData={{ outputMessage: [{ role, content: "Visible reply" }] }}
        />,
      );
      expect(screen.getByText("Unknown")).toBeVisible();
      expect(screen.getByText("Visible reply")).toBeVisible();
    },
  );

  it("keeps valid role capitalization unchanged", () => {
    render(
      <LLMSpan
        column={{ headerName: "Output" }}
        tabData={{ outputMessage: [{ role: "assistant", content: "Reply" }] }}
      />,
    );
    expect(screen.getByText("Assistant")).toBeVisible();
    expect(screen.getByText("Reply")).toBeVisible();
  });
});
