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
    capture_origin: "provider_received_track",
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
      coverage: { voiced_seconds: 12, analyzed_seconds: 20 },
    },
    estimated_snr_db: {
      ...metric("dB", 0),
      algorithm_version: "energy_pause_v1",
      coverage: {
        active_seconds: 14,
        inactive_seconds: 6,
        analyzed_seconds: 20,
      },
      flags: { noise_floor_floored: false, value_capped: false },
    },
    voice_quality_index: {
      ...metric("index", 3.4125),
      algorithm_version: "dnsmos_ovrl_mean_v1",
      model_version: "dnsmos_sig_bak_ovr:0123456789ab",
      calibration_id: "test-calibration-id",
      scale_id: "normalized_mos_0_5_v1",
      coverage: {
        window_count: 2,
        excluded_window_count: 0,
        detected_speech_seconds: 10,
        covered_speech_seconds: 8,
        cohort: "en:sip:provider_received_track",
      },
    },
  },
});
const rows = () => within(screen.getByRole("list")).getAllByRole("listitem");
const openDetails = async () =>
  userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Capture & version details" }));
const detailValue = (label) =>
  screen.getByText(label, { selector: "dt" }).nextElementSibling;

function allUnavailable(reason) {
  const data = envelope();
  data.state = "unavailable";
  Object.values(data.metrics).forEach((entry) =>
    Object.assign(entry, { state: "unavailable", reason, value: null }),
  );
  return data;
}

describe("Acoustic metrics (R09 / AC10)", () => {
  it("renders available values without transcript metrics, with precision and caveats", () => {
    render(<CallAnalyticsView audioMetrics={envelope()} />);
    const region = screen.getByRole("region", {
      name: "Acoustic metrics · Tested agent",
    });
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-labelledby");
    expect(rows()).toHaveLength(3);
    expect(rows()[0]).toHaveTextContent("Average pitch (Hz)182.4");
    expect(rows()[1]).toHaveTextContent("Estimated SNR (dB)0.0");
    expect(rows()[2]).toHaveTextContent("Voice quality index (0–5)3.41");
    expect(
      screen.getByText("Estimated perceptual quality, normalized 0–5"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Not MOS or a naturalness certification"),
    ).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(
      screen.queryByText("No call analytics data available"),
    ).not.toBeInTheDocument();
  });

  it("hides absent metrics and removes the section when the flag key disappears", () => {
    const { rerender } = render(
      <CallAnalyticsView audioMetrics={envelope()} />,
    );
    rerender(<CallAnalyticsView analysisSummary="Call completed" />);
    expect(
      screen.queryByRole("region", { name: /Acoustic metrics/ }),
    ).not.toBeInTheDocument();
  });

  it.each(["not_requested", "unavailable"])(
    "hides entirely not_enabled metrics (%s) without a blank analytics panel",
    (state) => {
      const data = allUnavailable("not_enabled");
      data.state = state;
      render(<CallAnalyticsView audioMetrics={data} isStale />);
      expect(
        screen.queryByRole("region", { name: /Acoustic metrics/ }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText("No call analytics data available"),
      ).toBeInTheDocument();
    },
  );

  it("keeps partial results when only one metric is not_enabled", () => {
    const data = envelope();
    data.state = "partial";
    data.metrics.voice_quality_index = {
      state: "unavailable",
      reason: "not_enabled",
      value: null,
    };
    render(<AcousticMetricsSection data={data} />);
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByText("Not enabled — Unavailable")).toBeInTheDocument();
    expect(screen.getByText("182.4")).toBeInTheDocument();
  });

  it.each([
    ["Hz", "0.0"],
    ["dB", "0.0"],
    ["index", "0.00"],
  ])("keeps valid zero for %s as %s", (unit, expected) => {
    expect(formatMetricValue(metric(unit, 0))).toBe(expected);
  });

  it("renders VQI zero but never turns missing or nonfinite values into zero", () => {
    const data = envelope();
    data.metrics.voice_quality_index.value = 0;
    data.metrics.average_pitch_hz.value = null;
    data.metrics.estimated_snr_db.value = Infinity;
    render(<AcousticMetricsSection data={data} />);
    expect(rows()[0]).toHaveTextContent("—");
    expect(rows()[1]).toHaveTextContent("—");
    expect(rows()[2]).toHaveTextContent("0.00");
  });

  it.each([...UNAVAILABLE_REASONS, ...FAILED_REASONS])(
    "puts human text first and %s only in the disclosure",
    async (reason) => {
      const data = envelope();
      data.state = "partial";
      const state = FAILED_REASONS.includes(reason) ? "failed" : "unavailable";
      data.metrics.average_pitch_hz = {
        state,
        reason,
        unit: "Hz",
        value: null,
      };
      render(<AcousticMetricsSection data={data} />);
      expect(rows()[0]).toHaveTextContent(REASON_LABELS[reason]);
      expect(rows()[0]).not.toHaveTextContent(reason);
      expect(rows()[0]).not.toHaveTextContent("Unavailable — Unavailable");
      expect(screen.queryByText(reason)).not.toBeInTheDocument();
      await openDetails();
      expect(detailValue("Average pitch (Hz) reason code")).toHaveTextContent(
        reason,
      );
      expect(detailValue("Average pitch (Hz) reason code")).toHaveStyle({
        fontFamily: "monospace",
      });
    },
  );

  it.each(UNAVAILABLE_REASONS.filter((reason) => reason !== "not_enabled"))(
    "shows an entirely unavailable result for %s",
    (reason) => {
      render(<AcousticMetricsSection data={allUnavailable(reason)} />);
      expect(rows()).toHaveLength(3);
      for (const row of rows())
        expect(row).toHaveTextContent(REASON_LABELS[reason]);
    },
  );

  it.each([
    ["unavailable", "Unavailable"],
    ["failed", "Analysis failed"],
  ])(
    "uses a nonduplicated fallback for unknown %s reasons",
    async (state, fallback) => {
      const data = envelope();
      data.state = state;
      data.metrics.average_pitch_hz = {
        state,
        reason: "future_code",
        unit: "Hz",
        value: null,
      };
      render(<AcousticMetricsSection data={data} />);
      expect(
        within(rows()[0]).getByText(fallback, { exact: true }),
      ).toBeInTheDocument();
      expect(screen.queryByText(/future_code/)).not.toBeInTheDocument();
      await openDetails();
      expect(screen.getByText("future_code")).toBeInTheDocument();
    },
  );

  it("distinguishes a failed analysis from missing audio", () => {
    const data = allUnavailable("no_recording");
    data.state = "failed";
    data.metrics.average_pitch_hz = {
      state: "failed",
      reason: "timeout",
      value: null,
    };
    render(<AcousticMetricsSection data={data} />);
    expect(screen.getByText("Failed", { exact: true })).toBeInTheDocument();
    expect(rows()[0]).toHaveTextContent("Analysis timed out — Failed");
    expect(rows()[1]).toHaveTextContent("No recording — Unavailable");
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
  });

  it.each([
    { schema_version: 99, state: "complete" },
    { schema_version: 1, state: "unsupported" },
  ])("does not interpret unsupported schema values (%j)", (fields) => {
    render(<AcousticMetricsSection data={{ ...envelope(), ...fields }} />);
    expect(rows()).toHaveLength(1);
    expect(screen.getByText("Unsupported metrics version")).toBeInTheDocument();
    expect(
      screen.getByText("Unavailable", { exact: true }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Complete")).not.toBeInTheDocument();
    expect(screen.queryByText("182.4")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Capture & version details" }),
    ).not.toBeInTheDocument();
  });

  it("keeps a truthful stale notice and cached values until recovery, with no retry promise", () => {
    const data = envelope();
    const { rerender } = render(
      <CallAnalyticsView audioMetrics={data} isStale />,
    );
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Couldn't refresh audio metrics. Showing the last result.",
    );
    expect(screen.queryByText(/Retrying/)).not.toBeInTheDocument();
    expect(screen.getByText("0.0")).toBeInTheDocument();
    rerender(<CallAnalyticsView audioMetrics={data} isStale />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    rerender(<CallAnalyticsView audioMetrics={data} isStale={false} />);
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it.each([
    ["provider_received_track", "Received over the call"],
    ["provider_source_track", "Agent source audio"],
    ["unknown", "Unknown origin"],
    ["future_origin", "Unknown origin"],
    ["toString", "Unknown origin"],
    [null, "—"],
  ])("humanizes capture origin %s", async (origin, label) => {
    const data = envelope();
    data.source.capture_origin = origin;
    render(<AcousticMetricsSection data={data} />);
    expect(
      screen.getByText(`Capture: ${label} · Phone (SIP)`),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("audio_metrics_pipeline_v1"),
    ).not.toBeInTheDocument();
    await openDetails();
    expect(detailValue("Capture origin")).toHaveTextContent(label);
  });

  it.each([
    ["sip", "Phone (SIP)"],
    ["web_bridge", "Web call"],
    ["future_transport", "Unknown transport"],
    [null, "—"],
  ])("humanizes transport %s", async (transport, label) => {
    const data = envelope();
    data.source.transport = transport;
    render(<AcousticMetricsSection data={data} />);
    expect(
      screen.getByText(`Capture: Received over the call · ${label}`),
    ).toBeInTheDocument();
    await openDetails();
    expect(detailValue("Transport")).toHaveTextContent(label);
  });

  it("shows coverage, versions, zero excluded windows and a local computed time", async () => {
    const data = envelope();
    render(<AcousticMetricsSection data={data} />);
    await openDetails();
    for (const [label, value] of [
      ["Pitch voiced audio (s)", "12"],
      ["Pitch analyzed audio (s)", "20"],
      ["SNR speech-level frames (s)", "14"],
      ["SNR noise-level frames (s)", "6"],
      ["SNR analyzed audio (s)", "20"],
      ["VQI windows", "2"],
      ["VQI excluded windows", "0"],
      ["VQI detected speech (s)", "10"],
      ["VQI covered speech (s)", "8"],
      ["VQI speech coverage", "80.0%"],
      ["VQI cohort", "en:sip:provider_received_track"],
      ["Calibration", "test-calibration-id"],
      ["Model version", "dnsmos_sig_bak_ovr:0123456789ab"],
      ["Scale", "normalized_mos_0_5_v1"],
      ["Generation", "0"],
      ["Target role", "Tested agent"],
    ])
      expect(detailValue(label)).toHaveTextContent(value);
    const time = screen.getByTitle(data.computed_at);
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", data.computed_at);
    expect(time).toHaveTextContent(
      new Date(data.computed_at).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }),
    );
    expect(time).not.toHaveTextContent(data.computed_at);
  });

  it.each([null, 0])(
    "does not invent a percentage without detected speech (%s)",
    async (detected) => {
      const data = envelope();
      data.metrics.average_pitch_hz.coverage = null;
      data.metrics.estimated_snr_db.coverage = { active_seconds: 0 };
      data.metrics.voice_quality_index.coverage = {
        detected_speech_seconds: detected,
        covered_speech_seconds: 0,
      };
      data.computed_at = "invalid-date";
      render(<AcousticMetricsSection data={data} />);
      await openDetails();
      expect(detailValue("Pitch voiced audio (s)")).toHaveTextContent("—");
      expect(detailValue("SNR speech-level frames (s)")).toHaveTextContent(
        /^0$/,
      );
      expect(detailValue("SNR noise-level frames (s)")).toHaveTextContent("—");
      expect(detailValue("VQI covered speech (s)")).toHaveTextContent(/^0$/);
      expect(detailValue("VQI windows")).toHaveTextContent("—");
      expect(detailValue("VQI cohort")).toHaveTextContent("—");
      expect(detailValue("VQI speech coverage")).toHaveTextContent("—");
      expect(detailValue("Computed at")).toHaveTextContent("—");
    },
  );

  it("preserves zero percent when detected speech exists but none was covered", async () => {
    const data = envelope();
    data.metrics.voice_quality_index.coverage.covered_speech_seconds = 0;
    render(<AcousticMetricsSection data={data} />);
    await openDetails();
    expect(detailValue("VQI speech coverage")).toHaveTextContent("0.0%");
  });

  it("shows SNR caveats only for explicit true flags", () => {
    const data = envelope();
    const { rerender } = render(<AcousticMetricsSection data={data} />);
    expect(screen.queryByText("Noise floor floored")).not.toBeInTheDocument();
    expect(screen.queryByText("Capped at 100 dB")).not.toBeInTheDocument();
    data.metrics.estimated_snr_db.flags = {
      noise_floor_floored: "true",
      value_capped: 1,
    };
    rerender(<AcousticMetricsSection data={data} />);
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

  it("opens and closes disclosures by keyboard with accessible controls", async () => {
    const user = userEvent.setup();
    render(<AcousticMetricsSection data={envelope()} />);
    const details = screen.getByRole("button", {
      name: "Capture & version details",
    });
    const limits = screen.getByRole("button", { name: "Measurement limits" });
    expect(details).toHaveAttribute("aria-expanded", "false");
    expect(
      document.getElementById(details.getAttribute("aria-controls")),
    ).not.toBeVisible();
    await user.tab();
    expect(details).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(details).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("vapi_role_v1")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(details).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("vapi_role_v1")).not.toBeInTheDocument();
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
