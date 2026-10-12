import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, within, userEvent } from "src/utils/test-utils";
import AcousticMetricsSection from "../AcousticMetricsSection";
import CallAnalyticsView from "../CallAnalyticsView";
import {
  UNAVAILABLE_REASONS,
  FAILED_REASONS,
  REASON_LABELS,
  formatMetricValue,
} from "../acousticMetricsLabels";

const metric = (unit, value) => ({
  state: "available",
  unit,
  value,
  reason: null,
});
const envelope = () => ({
  schema_version: 1,
  state: "complete",
  generation: 0,
  computed_at: "2026-10-03T12:00:00Z",
  pipeline_version: "audio_metrics_pipeline_v1",
  source: {
    capture_origin: "provider_recording",
    transport: "sip",
    provider: "vapi",
    target_role: "tested_agent",
    mapping_version: "vapi_role_v1",
    original_sample_rate_hz: 16000,
    original_channel_count: 1,
    target_sample_count: 1392000,
    input_duration_seconds: 87,
    preprocessing_version: "native_v1",
  },
  metrics: {
    average_pitch_hz: {
      ...metric("Hz", 182.4137),
      algorithm_version: "pyin_c2c6_v1",
    },
    estimated_snr_db: {
      ...metric("dB", 0),
      algorithm_version: "energy_pause_v1",
      flags: { noise_floor_floored: false, value_capped: false },
    },
    voice_quality_index: {
      ...metric("index", 3.4125),
      algorithm_version: "dnsmos_ovrl_mean_v1",
      model_version: "dnsmos_sig_bak_ovr:0123456789ab",
      calibration_id: "vqi-cal-2026Q4-r1",
      scale_id: "normalized_mos_0_5_v1",
    },
  },
});

describe("Acoustic metrics (R09 / AC10)", () => {
  it("renders without transcript metrics with exact labels, units and a valid zero", () => {
    render(<CallAnalyticsView audioMetrics={envelope()} />);
    const region = screen.getByRole("region", {
      name: "Acoustic metrics · Tested agent",
    });
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-labelledby");
    const rows = within(within(region).getByRole("list")).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Average pitch (Hz)");
    expect(rows[0]).toHaveTextContent("182.4");
    expect(rows[1]).toHaveTextContent("Estimated SNR (dB)");
    expect(rows[1]).toHaveTextContent("0.0");
    expect(rows[2]).toHaveTextContent("Voice quality index (0–5)");
    expect(rows[2]).toHaveTextContent("3.41");
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(
      screen.queryByText("No call analytics data available"),
    ).not.toBeInTheDocument();
  });

  it("hides the section when the key is absent", () => {
    render(<CallAnalyticsView analysisSummary="Call completed" />);
    expect(
      screen.queryByRole("region", { name: /Acoustic metrics/ }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["Hz", "0.0"],
    ["dB", "0.0"],
    ["index", "0.00"],
  ])("formats zero for %s as %s", (unit, expected) => {
    expect(formatMetricValue(metric(unit, 0))).toBe(expected);
  });

  it("labels all committed reason enums including the server fallbacks", () => {
    expect(UNAVAILABLE_REASONS).toHaveLength(20);
    expect(FAILED_REASONS).toHaveLength(7);
    expect(UNAVAILABLE_REASONS).toContain("other_unavailable");
    expect(FAILED_REASONS).toContain("other_failed");
    for (const reason of [...UNAVAILABLE_REASONS, ...FAILED_REASONS]) {
      expect(REASON_LABELS[reason]).toEqual(expect.any(String));
      expect(REASON_LABELS[reason].length).toBeGreaterThan(0);
    }
  });

  it.each([
    ...UNAVAILABLE_REASONS.map((reason) => [
      "unavailable",
      reason,
      "Unavailable",
    ]),
    ...FAILED_REASONS.map((reason) => ["failed", reason, "Failed"]),
  ])(
    "renders the %s reason %s with human text and code",
    (state, reason, label) => {
      const data = envelope();
      data.state = "partial";
      data.metrics.average_pitch_hz = {
        state,
        reason,
        unit: "Hz",
        value: null,
      };
      render(<AcousticMetricsSection data={data} />);
      expect(
        screen.getByText(`${label} — ${REASON_LABELS[reason]} (${reason})`),
      ).toBeInTheDocument();
      expect(screen.getByText("Partial")).toBeInTheDocument();
    },
  );

  it.each([
    ["unavailable", "Unavailable", "Unavailable"],
    ["failed", "Failed", "Analysis failed"],
  ])("falls back for unknown %s reasons", (state, label, fallback) => {
    const data = envelope();
    data.metrics.average_pitch_hz = {
      state,
      reason: "future_code",
      unit: "Hz",
      value: null,
    };
    render(<AcousticMetricsSection data={data} />);
    expect(
      screen.getByText(`${label} — ${fallback} (future_code)`),
    ).toBeInTheDocument();
  });

  it("renders processing text for pending rows while preserving available siblings", () => {
    const data = envelope();
    data.state = "pending";
    data.metrics.voice_quality_index = {
      state: "pending",
      reason: null,
      value: null,
      unit: "index",
    };
    render(<AcousticMetricsSection data={data} />);
    expect(screen.getAllByText("Processing audio metrics")).toHaveLength(2);
    expect(screen.getByText("0.0")).toBeInTheDocument();
    expect(screen.queryByText(/\(null\)/)).not.toBeInTheDocument();
  });

  it("renders an unsupported envelope as one row without parsing metrics", () => {
    render(
      <AcousticMetricsSection
        data={{ schema_version: 99, state: "unsupported" }}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("Unsupported metrics version")).toBeInTheDocument();
    expect(screen.queryByText("Average pitch (Hz)")).not.toBeInTheDocument();
  });

  it("retains values when a refresh fails and clears the stale chip after recovery", () => {
    const data = envelope();
    const { rerender } = render(
      <CallAnalyticsView audioMetrics={data} isStale />,
    );
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("0.0")).toBeInTheDocument();
    rerender(<CallAnalyticsView audioMetrics={data} isStale={false} />);
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();
  });

  it("shows SNR flags only when true", () => {
    const data = envelope();
    const { rerender } = render(<AcousticMetricsSection data={data} />);
    expect(screen.queryByText("Noise floor floored")).not.toBeInTheDocument();
    expect(screen.queryByText("Capped at 100 dB")).not.toBeInTheDocument();
    data.metrics.estimated_snr_db.flags = {
      noise_floor_floored: true,
      value_capped: true,
    };
    rerender(<AcousticMetricsSection data={data} />);
    expect(screen.getByText("Noise floor floored")).toBeInTheDocument();
    expect(screen.getByText("Capped at 100 dB")).toBeInTheDocument();
  });

  it("opens both disclosures by keyboard with accessible expanded state", async () => {
    const user = userEvent.setup();
    render(<AcousticMetricsSection data={envelope()} />);
    const details = screen.getByRole("button", {
      name: "Capture & version details",
    });
    const limits = screen.getByRole("button", { name: "Measurement limits" });
    expect(details).toHaveAttribute("aria-expanded", "false");
    expect(limits).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("vapi_role_v1")).not.toBeInTheDocument();
    await user.tab();
    expect(details).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(details).toHaveAttribute("aria-expanded", "true");
    for (const text of [
      "vapi_role_v1",
      "native_v1",
      "pyin_c2c6_v1",
      "energy_pause_v1",
      "dnsmos_ovrl_mean_v1",
      "dnsmos_sig_bak_ovr:0123456789ab",
      "vqi-cal-2026Q4-r1",
      "normalized_mos_0_5_v1",
      "2026-10-03T12:00:00Z",
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    await user.tab();
    expect(limits).toHaveFocus();
    await user.keyboard(" ");
    expect(limits).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByText("Pitch is not a quality ranking."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SNR is an energy-contrast estimate that needs pauses."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "VQI is a normalized perceptual prediction, not MOS or a naturalness certification.",
      ),
    ).toBeInTheDocument();
  });
});
