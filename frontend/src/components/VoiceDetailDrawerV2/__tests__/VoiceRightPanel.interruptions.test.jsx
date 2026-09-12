import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
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

const baseData = {
  id: "call-1",
  module: "simulate",
  status: "completed",
  provider: "vapi",
  attributes: {},
  observation_span: [],
  transcript: [],
  user_interruption_count: 0,
  ai_interruption_count: 0,
};

describe("VoiceRightPanel interruption KPIs", () => {
  it("hides User Int. / AI Int. for a chat (text) simulation", () => {
    renderWithQueryClient(
      <VoiceRightPanel data={{ ...baseData, simulation_call_type: "text" }} />,
    );

    expect(screen.queryByText("User Int.")).not.toBeInTheDocument();
    expect(screen.queryByText("AI Int.")).not.toBeInTheDocument();
  });

  it("still shows User Int. / AI Int. for a voice simulation", () => {
    renderWithQueryClient(
      <VoiceRightPanel
        data={{ ...baseData, simulation_call_type: "voice" }}
      />,
    );

    expect(screen.getByText("User Int.")).toBeInTheDocument();
    expect(screen.getByText("AI Int.")).toBeInTheDocument();
  });
});
