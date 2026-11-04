import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import LLMSpan from "../RightSpans/LLMSpan";
import { getLlmData } from "../getSpanData";
import captures from "src/components/traceDetail/__tests__/fixtures/native-anthropic.json";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-audio/TestAudioPlayer", () => ({
  default: () => null,
}));
vi.mock("src/components/VideoPlayer/CustomVideoPlayer", () => ({
  default: () => null,
}));

afterEach(cleanup);

describe.each(captures)(
  "native Anthropic $sdkVersion legacy drawer",
  (capture) => {
    const observation = { ...capture, span_attributes: capture.attributes };
    it("extracts normalized native response messages without mutating stored content", () => {
      const before = JSON.stringify(observation);
      expect(
        getLlmData(observation, "output").outputMessage[0]?.content,
      ).toEqual(["RESPONSE_SENTINEL"]);
      expect(JSON.stringify(observation)).toBe(before);
    });
    it("renders both the message and raw output tabs", () => {
      render(
        <ThemeProvider theme={createTheme()}>
          <MemoryRouter>
            <LLMSpan
              column={{ headerName: "Output" }}
              tabData={getLlmData(observation, "output")}
            />
          </MemoryRouter>
        </ThemeProvider>,
      );
      expect(screen.getByText("RESPONSE_SENTINEL")).toBeVisible();
      fireEvent.click(screen.getByRole("tab", { name: "Output", exact: true }));
      expect(screen.getByText("RESPONSE_SENTINEL")).toBeVisible();
    });
  },
);
