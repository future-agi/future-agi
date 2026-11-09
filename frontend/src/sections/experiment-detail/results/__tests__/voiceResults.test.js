import { describe, expect, it } from "vitest";
import { voiceQuickFilters, SLOW_P90_MS } from "../voiceFilters";
import { VOICE_COLUMNS, resultsColumnCatalog } from "../resultsColumns";

const call = (o = {}) => ({
  textOnly: false,
  passed: true,
  p50: 800,
  p90: 1500,
  kept: 0,
  hasAudio: true,
  recordingMissing: false,
  failedTool: false,
  cost: { total: 0.1, stt: 0.01, llm: 0.05, tts: 0.03, telephony: 0.01 },
  ...o,
});

describe("voiceQuickFilters", () => {
  const calls = {
    r1: { a: call(), b: call({ p90: SLOW_P90_MS + 1, kept: 2 }) },
    r2: { a: call({ failedTool: true }), b: call() },
    r3: {
      a: call({ hasAudio: false, recordingMissing: true }),
      b: call({ textOnly: true, hasAudio: false, p90: null, kept: null }),
    },
  };

  it("counts rows where any run in view matches, and the pill keeps exactly them", () => {
    const f = Object.fromEntries(
      voiceQuickFilters(calls).map((x) => [x.id, x]),
    );
    expect(f.has_audio.count).toBe(2);
    expect(f.slow_p90.ids).toEqual(["r1"]);
    expect(f.kept_talking.ids).toEqual(["r1"]);
    expect(f.failed_tool.ids).toEqual(["r2"]);
    expect(f.recording_missing.ids).toEqual(["r3"]);
    Object.values(f).forEach((x) => {
      expect(x.pill).toMatchObject({ type: "rows", label: x.label });
      expect(x.pill.ids.length).toBe(x.count);
    });
  });

  it("honours the runs in view and drops filters with no rows", () => {
    const ids = voiceQuickFilters(calls, { runIds: ["a"] }).map((x) => x.id);
    expect(ids).not.toContain("slow_p90");
    expect(ids).not.toContain("kept_talking");
    expect(voiceQuickFilters(null)).toEqual([]);
  });
});

describe("voice columns", () => {
  it("text-only calls keep completion and cost, and the cost tooltip splits by stage", () => {
    const cost = VOICE_COLUMNS.find((c) => c.key === "voice:cost");
    expect(cost.text).toBe(true);
    expect(cost.tip(call())).toBe(
      "STT $0.01 · LLM $0.05 · TTS $0.03 · Telephony $0.01",
    );
    expect(VOICE_COLUMNS.find((c) => c.key === "voice:p90").text).toBeFalsy();
  });

  it("on voice data the call columns are the default and the eval / wall time columns are one click away", () => {
    const summary = {
      runs: [{ run_id: "a" }, { run_id: "b" }],
      metrics: [{ key: "eval:x", name: "X", output_type: "pass_fail" }],
      system_metrics: [{ key: "ops:latency", name: "Latency" }],
    };
    const cat = resultsColumnCatalog(summary, { voice: true });
    const vis = (id) => cat.find((c) => c.id === id)?.defaultVisible;
    expect(vis("voice:p90")).not.toBe(false);
    expect(vis("voice:wer")).toBe(false);
    expect(vis("voice:talk")).toBe(false);
    const plain = resultsColumnCatalog(summary, {});
    expect(plain.some((c) => c.id.startsWith("voice:"))).toBe(false);
  });
});
