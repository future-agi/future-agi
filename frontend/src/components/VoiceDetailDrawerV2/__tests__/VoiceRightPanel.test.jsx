import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import VoiceRightPanel from "../VoiceRightPanel";

vi.mock("src/sections/falcon-ai/helpers/openFixWithFalcon", () => ({
  openFixWithFalcon: vi.fn(),
}));

vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: () => <div>Annotations</div>,
}));

const renderWithQueryClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("VoiceRightPanel", () => {
  it("falls back to simulation attributes when the linked span has empty attributes", async () => {
    renderWithQueryClient(
      <VoiceRightPanel
        data={{
          id: "call-1",
          module: "simulate",
          status: "completed",
          provider: "vapi",
          attributes: {
            raw_log: { room_sid: "RM_test" },
            "vapi.call_id": "call-1",
          },
          observation_span: [
            {
              id: "span-1",
              parent_span_id: null,
              observation_type: "conversation",
              span_attributes: {},
            },
          ],
          transcript: [],
        }}
      />,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Attributes" }));

    expect(screen.getByText("raw_log")).toBeInTheDocument();
    expect(screen.getByText("vapi.call_id")).toBeInTheDocument();
  });
});
