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

  it("shows utterance-level error localization when an environment eval is expanded", async () => {
    renderWithQueryClient(
      <VoiceRightPanel
        data={{
          id: "call-1",
          module: "simulate",
          status: "completed",
          provider: "livekit",
          transcript: [],
          eval_metrics: {
            "eval-1": {
              id: "eval-1",
              name: "Greeting quality",
              value: "Failed",
              type: "Pass/Fail",
              reason: "The greeting was not appropriate.",
              error_localizer: true,
              error_localizer_status: "completed",
              selected_input_key: "conversation",
              input_data: {
                conversation:
                  "Simulator: Hello.\nAgent: Stop wasting my time.\nSimulator: Goodbye.",
              },
              input_types: { conversation: "text" },
              error_analysis: {
                conversation: [
                  {
                    orgSen: { startIdx: 25, endIdx: 46 },
                    reason: "The agent utterance is hostile.",
                  },
                ],
              },
            },
          },
        }}
      />,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Evals" }));
    await userEvent.click(screen.getByText("Greeting quality"));

    expect(screen.getByText("Possible Error")).toBeInTheDocument();
    expect(screen.getByText("Stop wasting my time.")).toBeInTheDocument();
  });

  it("shows image-region error localization when an image eval is expanded", async () => {
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({ clearRect: vi.fn() });

    renderWithQueryClient(
      <VoiceRightPanel
        data={{
          id: "call-image",
          module: "simulate",
          status: "completed",
          provider: "browser",
          transcript: [],
          eval_metrics: {
            "eval-image": {
              id: "eval-image",
              name: "Screenshot quality",
              value: "Failed",
              type: "Pass/Fail",
              error_localizer: true,
              error_localizer_status: "completed",
              selected_input_key: "screenshot",
              input_data: {
                screenshot:
                  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
              },
              input_types: { screenshot: "image" },
              error_analysis: {
                screenshot: [
                  {
                    orgPatch: {
                      coordinates: {
                        topLeft: [0, 0],
                        bottomRight: [1, 1],
                      },
                    },
                    reason: "The button is obscured.",
                  },
                ],
              },
            },
          },
        }}
      />,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Evals" }));
    await userEvent.click(screen.getByText("Screenshot quality"));

    expect(screen.getByText("Possible Error")).toBeInTheDocument();
    expect(screen.getByAltText("Overlayed")).toHaveAttribute(
      "src",
      expect.stringContaining("data:image/gif;base64"),
    );
    getContext.mockRestore();
  });
});
