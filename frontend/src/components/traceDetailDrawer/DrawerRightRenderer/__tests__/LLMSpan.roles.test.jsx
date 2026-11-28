import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import LLMSpan from "../RightSpans/LLMSpan";
import { getLlmData } from "../getSpanData";
import { openaiImageAttributes } from "./openaiMultimodalSpan";

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

describe("unit: legacy LLM span with parsed multimodal parts (TH-2379)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the text part of the actual OpenAI SDK span", () => {
    // The legacy card lazy-loads image parts; its library reads
    // IntersectionObserverEntry.prototype, which jsdom does not define.
    vi.stubGlobal(
      "IntersectionObserverEntry",
      class IntersectionObserverEntry {
        get isIntersecting() {
          return false;
        }
      },
    );
    const { inputMessage } = getLlmData(
      { span_attributes: openaiImageAttributes },
      "input",
    );
    render(
      <LLMSpan column={{ headerName: "Input" }} tabData={{ inputMessage }} />,
    );
    expect(screen.getByText("What colour is this square?")).toBeVisible();
    expect(
      screen.getByText("Describe the image the user sends."),
    ).toBeVisible();
  });
});
