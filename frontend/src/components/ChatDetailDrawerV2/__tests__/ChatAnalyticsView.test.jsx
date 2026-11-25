import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ChatAnalyticsView from "../ChatAnalyticsView";

const twoExchanges = [
  { id: "m1", role: "user", content: "Hello" },
  { id: "m2", role: "assistant", content: "Hi" },
  { id: "m3", role: "user", content: "Thanks" },
  { id: "m4", role: "assistant", content: "Welcome" },
];

const turnsValue = () => screen.getByText("Turns").nextElementSibling;

describe("ChatAnalyticsView Turns", () => {
  it("counts exchanges, not transcript rows, when turn_count is missing", () => {
    render(
      <ChatAnalyticsView
        data={{ turn_count: null, transcript: twoExchanges }}
      />,
    );
    expect(turnsValue()).toHaveTextContent(/^2$/);
  });

  it("prefers the turn_count the API sends", () => {
    render(
      <ChatAnalyticsView data={{ turn_count: 3, transcript: twoExchanges }} />,
    );
    expect(turnsValue()).toHaveTextContent(/^3$/);
  });

  it("shows a dash when there is no transcript and no count", () => {
    render(
      <ChatAnalyticsView data={{ duration_seconds: 5, transcript: [] }} />,
    );
    expect(turnsValue()).toHaveTextContent("—");
  });
});
