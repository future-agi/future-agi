import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get, post: vi.fn() },
  endpoints: {
    project: {
      getEvalDetails: (span, config) => `/eval-details/${span}/${config}/`,
      reRunTracerEvalutation: "/rerun/",
    },
    develop: { eval: {} },
  },
}));

import EvalErrorLocalization from "../EvalErrorLocalization";

const OUTPUT = "The moon is made of cheese.";

function renderTrace(result, props = {}) {
  if (result !== undefined) get.mockResolvedValue({ data: { result } });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EvalErrorLocalization
        observationSpanId="span-1"
        customEvalConfigId="config-1"
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("EvalErrorLocalization on an Observe span eval", () => {
  beforeEach(() => get.mockReset());

  it("highlights the localized sentence of a finished localization", async () => {
    renderTrace({
      error_analysis: {
        input_1: [
          {
            unit_key: "s1",
            rank: "1",
            orgSen: { text: "made of cheese", start_idx: 12, end_idx: 26 },
            reason: "The moon is rock, not cheese.",
          },
        ],
      },
      selected_input_key: "output",
      input_data: { output: OUTPUT },
      input_types: { output: "text" },
      error_localizer_status: "completed",
      error_localizer_message: null,
    });

    expect(await screen.findByText("made of cheese")).toBeInTheDocument();
    expect(screen.getByText(/The moon is/)).toBeInTheDocument();
    // The card is titled with the evaluated input, not the analysis slot.
    expect(screen.getByText("output")).toBeInTheDocument();
    expect(screen.queryByText("input_1")).toBeNull();
  });

  it("shows a failed localization with its reason instead of 'not yet run'", async () => {
    renderTrace({
      error_analysis: null,
      selected_input_key: null,
      input_data: null,
      input_types: null,
      error_localizer_status: "failed",
      error_localizer_message: "Error localization failed (timeout).",
    });

    expect(
      await screen.findByText("Error localization failed"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Error localization failed (timeout)."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No error localization yet")).toBeNull();
    // Observe rows carry no project version, so a re-run cannot start.
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("offers Retry for a failed localization when the eval can be re-run", async () => {
    renderTrace(
      {
        error_analysis: null,
        error_localizer_status: "failed",
        error_localizer_message: "Error localization failed (timeout).",
      },
      { projectVersionId: "version-1" },
    );

    expect(
      await screen.findByRole("button", { name: "Retry" }),
    ).toBeInTheDocument();
  });

  it("explains a finished localization that pinned no segment", async () => {
    renderTrace({
      error_analysis: { input_1: [] },
      selected_input_key: "output",
      input_data: { output: OUTPUT },
      error_localizer_status: "completed",
      error_localizer_message:
        "No part of the input could be pinned to this result.",
    });

    expect(
      await screen.findByText(
        "No part of the input could be pinned to this result.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("No error localization yet")).toBeNull();
  });

  it("polls a running localization until it finishes", async () => {
    get
      .mockResolvedValueOnce({
        data: {
          result: { error_analysis: null, error_localizer_status: "running" },
        },
      })
      .mockResolvedValue({
        data: {
          result: {
            error_analysis: null,
            error_localizer_status: "failed",
            error_localizer_message: "Error localization failed (timeout).",
          },
        },
      });
    renderTrace(undefined);

    expect(
      await screen.findByText("Error localization running…"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Error localization failed (timeout).",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  }, 10000);
});
