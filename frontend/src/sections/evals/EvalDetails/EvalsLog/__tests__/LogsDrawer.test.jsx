/* eslint-disable react/prop-types */
import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => getMock(...args) },
  endpoints: { develop: { eval: { getEvalLogs: "/eval/log/" } } },
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

vi.mock("src/sections/common/DatapointCard", () => ({
  default: ({ value, column }) => (
    <div data-testid={`datapoint-${column?.headerName}`}>
      {column?.headerName}: {value?.cellValue}
    </div>
  ),
}));

vi.mock("src/components/custom-audio/AudioDatapointCard", () => ({
  default: () => <div data-testid="audio-datapoint" />,
}));

vi.mock("src/sections/common/ImageDatapointCard", () => ({
  default: () => <div data-testid="image-datapoint" />,
}));

vi.mock("src/components/custom-audio/context-provider/AudioPlaybackContext", () => ({
  AudioPlaybackProvider: ({ children }) => <>{children}</>,
}));

vi.mock("../../EvalsFeedback/AddEvalsFeedbackDrawer", () => ({
  default: ({ open }) => (open ? <div data-testid="feedback-drawer" /> : null),
}));

// theme with the app's real typography/palette — LogDrawerRight (via
// getStatusColor/getLabel) reaches into theme.palette.red[500] etc. that the
// shared test-utils theme doesn't define.
const appTheme = createTheme({ palette: palette("light"), typography });

import LogsDrawer from "../LogsDrawer";

function renderDrawer(props = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(
    <Wrapper>
      <LogsDrawer
        open
        selectedRow={{ logId: "log-1", order: 0 }}
        evalsId="eval-1"
        refreshGrid={vi.fn()}
        evalOutputTypes={{}}
        onClose={vi.fn()}
        {...props}
      />
    </Wrapper>,
    { theme: appTheme },
  );
}

const baseResult = {
  evaluation_id: "ev-1",
  experiment_id: "",
  prompt_template_id: "",
  prompt_version_id: "",
  dataset_id: "",
  trace_id: "",
  span_id: "",
  source: "eval_playground",
  created_at: "2026-08-01T00:00:00Z",
  output: { output: "Passed", reason: "Because the math checks out" },
  error_details: {},
  required_keys: ["question"],
  values: { question: "What is 2+2?" },
  input_data_types: { question: "text" },
};

describe("LogsDrawer", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("shows the loading skeleton before the log fetch resolves", () => {
    getMock.mockReturnValue(new Promise(() => {})); // never resolves

    renderDrawer();

    expect(screen.queryByText("Evaluation Logs")).not.toBeInTheDocument();
    // LoaderDrawer renders MUI Skeletons in place of the header/content.
    expect(document.querySelectorAll(".MuiSkeleton-root").length).toBeGreaterThan(0);
  });

  it("fetches the log by id/order and renders its fields once loaded", async () => {
    getMock.mockResolvedValue({ data: { result: baseResult } });

    renderDrawer();

    expect(await screen.findByText("Evaluation Logs")).toBeInTheDocument();
    expect(getMock).toHaveBeenCalledWith(
      "/eval/log/",
      expect.objectContaining({
        params: { log_id: "log-1", order: 0, source: "logs" },
      }),
    );

    // Top menu chips — id + source (mapped through the copy-chip list).
    expect(screen.getByText(/Evaluation Id: ev-1/)).toBeInTheDocument();
    expect(screen.getByText(/Source: eval_playground/)).toBeInTheDocument();

    // LogDrawerRight — score label + explanation, rendered from `output`.
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("Because the math checks out")).toBeInTheDocument();

    // Required-key datapoint card for the row's input.
    expect(screen.getByTestId("datapoint-question")).toHaveTextContent(
      "question: What is 2+2?",
    );
  });

  it("routes audio/image input columns to their dedicated datapoint cards", async () => {
    getMock.mockResolvedValue({
      data: {
        result: {
          ...baseResult,
          required_keys: ["clip", "photo"],
          values: { clip: "a.mp3", photo: "b.png" },
          input_data_types: { clip: "audio", photo: "image" },
        },
      },
    });

    renderDrawer();

    expect(await screen.findByTestId("audio-datapoint")).toBeInTheDocument();
    expect(screen.getByTestId("image-datapoint")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    getMock.mockResolvedValue({ data: { result: baseResult } });
    const onClose = vi.fn();

    renderDrawer({ onClose });

    await screen.findByText("Evaluation Logs");
    const closeButtons = screen.getAllByRole("button");
    await userEvent.click(closeButtons[0]);

    expect(onClose).toHaveBeenCalled();
  });

  it("renders nothing (no query) when there is no selected row", () => {
    renderDrawer({ selectedRow: null, open: false });
    expect(getMock).not.toHaveBeenCalled();
  });
});
