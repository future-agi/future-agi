// Static test data for component and model contracts; no simulation runtime.
export const CALL_VALUE_EXAMPLE = Object.freeze({
  kind: "call",
  call_id: "call_1b_000003_1",
  provider: "livekit",
  depth: "full_voice",
  status: "completed",
  ended_reason: "caller hung up",
  duration_ms: 108000,
  recording: {
    caller: {
      kind: "audio",
      mime: "audio/wav",
      url: "/model-hub/r4/calls/call_1b_000003_1/audio/caller.wav",
      duration_ms: 108000,
      channels: 1,
      peaks: [0, 0.4, 0.7, 0.2],
    },
    agent: {
      kind: "audio",
      mime: "audio/wav",
      url: "/model-hub/r4/calls/call_1b_000003_1/audio/agent.wav",
      duration_ms: 108000,
      channels: 1,
      peaks: [0.1, 0, 0.6, 0.5],
    },
  },
  recording_status: "ok",
  turns: [
    {
      i: 0,
      role: "caller",
      text: "Hi, my blender jug arrived cracked. Order A as in apple, eight eight three zero.",
      scripted_text:
        "Hi, my blender jug arrived cracked. Order A as in apple, eight eight three zero.",
      start_ms: 0,
      end_ms: 6200,
      asr_confidence: 0.71,
    },
    {
      i: 1,
      role: "tool",
      text: 'lookup_order("8830") → 404',
      start_ms: 6900,
      end_ms: 7300,
      tool: {
        name: "lookup_order",
        args: {
          order_id: "8830",
        },
        result: {
          error: "not found",
        },
        status: "error",
      },
    },
    {
      i: 2,
      role: "agent",
      text: "I'm sorry, I can't find that order. Could you read the number again?",
      start_ms: 7600,
      end_ms: 11800,
      gap_ms: 700,
      stage_ms: {
        stt: 180,
        llm: 340,
        tts: 180,
      },
    },
  ],
  metrics: {
    latency_ms: {
      p50: 860,
      p90: 1880,
      p99: 4820,
      mean: 1010,
      n: 6,
    },
    ttfw_ms: 640,
    stage_p50_ms: {
      stt: 180,
      llm: 490,
      tts: 190,
    },
    interruptions: {
      caller_barge_ins: 1,
      agent_kept_talking: 0,
      avg_stop_ms: 310,
    },
    talk_ratio: {
      agent: 0.46,
      caller: 0.54,
    },
    dead_air_max_ms: 1400,
    wer: 0.182,
    cost_usd: {
      stt: 0.01,
      llm: 0.04,
      tts: 0.05,
      telephony: 0.02,
      total: 0.12,
    },
  },
  latency_source: "recording",
  trace_id: null,
});
