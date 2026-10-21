import { describe, expect, it } from "vitest";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";
import {
  normalizeVoiceCallStatus,
  normalizeVoiceCallSavedFilters,
  VOICE_CALL_FILTER_FIELDS,
} from "../voiceCallFilterFields";

const FILTER_LABELS = {
  call_id: "Call ID",
  status: "Status",
  duration_seconds: "Duration (seconds)",
  avg_agent_latency_ms: "Avg Agent Latency (ms)",
  turn_count: "Turn Count",
  talk_ratio: "Agent Talk (%) — rounded",
  "gen_ai.usage.total_tokens": "Tokens",
  cost_cents: "Cost (cents)",
  user_interruption_count: "User Interrupts",
  ai_interruption_count: "Agent Interrupts",
  ended_reason: "Ended Reason",
  call_type: "Type",
  user_wpm: "User WPM",
  bot_wpm: "Agent WPM",
  agent_talk_percentage: "Agent Talk Percentage",
};

const VISIBLE_COLUMN_LABELS = {
  ...FILTER_LABELS,
  duration_seconds: "Duration",
  avg_agent_latency_ms: "Avg Latency",
  talk_ratio: "Talk Ratio",
  cost_cents: "Cost",
  agent_talk_percentage: "Agent Talk (%)",
};

const savedFilter = (columnId, value, colType = "SYSTEM_METRIC") => ({
  id: `filter-${columnId}`,
  column_id: columnId,
  display_name: columnId,
  filter_config: {
    filter_type: "text",
    filter_op: "in",
    filter_value: value,
    col_type: colType,
  },
});

describe("voice-call frontend field contract", () => {
  it("normalizes provider statuses to the backend's closed vocabulary", () => {
    expect(
      normalizeVoiceCallStatus([
        "ended",
        "done",
        "complete",
        "completed",
        "success",
        "succeeded",
        "ok",
        "in-progress",
        "in_progress",
        "ongoing",
        "started",
        "initiated",
        "processing",
        "scheduled",
        "created",
        "dialing",
        "connecting",
        "ringing",
        "queued",
        "pending",
        "failed",
        "failure",
        "error",
        "errored",
        "dropped",
        "cancelled",
        "canceled",
        "aborted",
        "hung-up",
        "hung_up",
        "not-connected",
        "not_connected",
        "no-answer",
        "no_answer",
        "unanswered",
        "busy",
        "future-provider-transition",
      ]),
    ).toEqual([
      "completed",
      "in-progress",
      "failed",
      "dropped",
      "not-connected",
    ]);
    expect(normalizeVoiceCallStatus("   ")).toBe("");
    expect(normalizeVoiceCallStatus(null)).toBeNull();
  });

  it("defines all 15 picker and formatted-grid labels with their API type", () => {
    expect(
      Object.fromEntries(
        VOICE_CALL_FILTER_FIELDS.map((field) => [
          field.responseKey,
          field.label,
        ]),
      ),
    ).toEqual(FILTER_LABELS);
    expect(
      Object.fromEntries(
        VOICE_CALL_FILTER_FIELDS.map((field) => [
          field.responseKey,
          field.columnLabel || field.label,
        ]),
      ),
    ).toEqual(VISIBLE_COLUMN_LABELS);

    expect(VOICE_CALL_FILTER_FIELDS).toHaveLength(15);
    expect(
      VOICE_CALL_FILTER_FIELDS.every(
        (field) =>
          field.category === "system" && field.apiColType === "SYSTEM_METRIC",
      ),
    ).toBe(true);
    expect(
      Object.fromEntries(
        VOICE_CALL_FILTER_FIELDS.filter((field) => field.filterUnit).map(
          (field) => [field.value, field.filterUnit],
        ),
      ),
    ).toEqual({
      duration: "seconds",
      avg_agent_latency_ms: "milliseconds",
      talk_ratio: "rounded-percent",
      cost_cents: "cents",
      agent_talk_percentage: "percent",
    });
  });

  it("encodes every Tracing picker field with its canonical request id", () => {
    const requests = VOICE_CALL_FILTER_FIELDS.map((field) =>
      buildApiFilterFromPanelRow({
        field: field.value,
        fieldName: field.label,
        fieldType: field.type,
        fieldCategory: field.category,
        apiColType: field.apiColType,
        operator: field.type === "number" ? "equals" : "in",
        value: field.type === "number" ? 1 : ["sample"],
      }),
    );

    expect(requests).toHaveLength(15);
    requests.forEach((request, index) => {
      const field = VOICE_CALL_FILTER_FIELDS[index];
      expect(request).toMatchObject({
        column_id: field.value,
        display_name: field.label,
        filter_config: {
          filter_type: field.type === "number" ? "number" : "text",
          filter_op: field.type === "number" ? "equals" : "in",
          col_type: "SYSTEM_METRIC",
        },
      });
    });
  });

  it("upgrades only unambiguous simulator saved-view aliases", () => {
    const [status, duration, latency, tokens, userInterrupts, aiInterrupts] =
      normalizeVoiceCallSavedFilters([
        savedFilter("call_status", ["ended", "ERROR", "no_answer"]),
        savedFilter("duration_seconds", 42),
        savedFilter("agent_latency", 350),
        savedFilter("total_tokens", 1200),
        savedFilter("user_interruptions", 2),
        savedFilter("ai_interruptions", 3),
      ]);

    expect(status).toMatchObject({
      column_id: "call_status",
      display_name: "Status",
      filter_config: {
        filter_type: "text",
        filter_value: ["completed", "failed", "not-connected"],
        col_type: "SYSTEM_METRIC",
      },
    });
    expect(duration).toMatchObject({
      column_id: "duration",
      display_name: "Duration (seconds)",
      filter_config: {
        filter_type: "number",
        filter_value: 42,
        col_type: "SYSTEM_METRIC",
      },
    });
    expect(latency).toMatchObject({
      column_id: "avg_agent_latency_ms",
      display_name: "Avg Agent Latency (ms)",
      filter_config: { filter_type: "number", filter_value: 350 },
    });
    expect(tokens).toMatchObject({
      column_id: "gen_ai.usage.total_tokens",
      display_name: "Tokens",
      filter_config: { filter_type: "number", filter_value: 1200 },
    });
    expect(userInterrupts).toMatchObject({
      column_id: "user_interruption_count",
      display_name: "User Interrupts",
      filter_config: { filter_type: "number", filter_value: 2 },
    });
    expect(aiInterrupts).toMatchObject({
      column_id: "ai_interruption_count",
      display_name: "Agent Interrupts",
      filter_config: { filter_type: "number", filter_value: 3 },
    });
  });

  it("hydrates transition and unknown saved statuses as in-progress", () => {
    expect(
      normalizeVoiceCallSavedFilters([
        savedFilter("call_status", [
          "initiated",
          "processing",
          "scheduled",
          "future-provider-transition",
        ]),
      ]),
    ).toEqual([
      expect.objectContaining({
        column_id: "call_status",
        filter_config: expect.objectContaining({
          col_type: "SYSTEM_METRIC",
          filter_value: ["in-progress"],
        }),
      }),
    ]);
  });

  it("never guesses that OTel status or raw call.status means lifecycle status", () => {
    const otelStatus = savedFilter("status", ["OK"]);
    const rawStatus = savedFilter("call.status", ["ended"], "SPAN_ATTRIBUTE");
    const rawDuration = savedFilter("duration_seconds", 42, "SPAN_ATTRIBUTE");
    const providerAmbiguousCost = savedFilter("total_cost", 0.122);

    const normalized = normalizeVoiceCallSavedFilters([
      otelStatus,
      rawStatus,
      rawDuration,
      providerAmbiguousCost,
    ]);

    expect(normalized).toEqual([
      otelStatus,
      rawStatus,
      rawDuration,
      providerAmbiguousCost,
    ]);
  });
});
