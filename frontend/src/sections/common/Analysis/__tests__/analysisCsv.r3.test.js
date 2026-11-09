import { describe, expect, it } from "vitest";
import {
  analysisCsv,
  csvDataLines,
  csvFileName,
  fileSizeText,
  worseTasksCsv,
} from "../analysisCsv";

const analysis = {
  baseline_run_id: "B",
  metrics: [],
  runs: [
    {
      run_id: "B",
      label: "#2B",
      name: "Agent",
      success_rate: 0.656,
      success_pass: 59,
      success_n: 90,
      cost_per_row: 0.011,
      latency_p50_ms: 9833.6,
    },
    {
      run_id: "D",
      label: "#2D",
      name: "Claude, Code",
      success_rate: 0.91,
      success_pass: 81,
      success_n: 89,
    },
  ],
  worklist: {
    items: [
      { example_id: "r1", row_order: 26, input_preview: 'Where is "8830"?' },
    ],
  },
};

describe("analysisCsv R3", () => {
  it("adds success, reliable, cost per task and time per task", () => {
    const text = analysisCsv(analysis);
    const [head, b] = text.split("\n");
    expect(head).toContain("success_rate,success_tries_passed,success_tries");
    expect(head).toContain("cost_per_task_usd,time_per_task_ms");
    expect(b).toContain("0.656,59,90");
    expect(text).toContain('"Claude, Code"');
    expect(csvDataLines(text)).toBe(2);
  });
  it("lists the tasks that got worse", () => {
    const text = worseTasksCsv(analysis, [
      { run: analysis.runs[1], base: analysis.runs[0], worseIds: ["r1", "r2"] },
    ]);
    expect(text.split("\n")).toEqual([
      "run,baseline,row_id,row,input_preview",
      '#2D,#2B,r1,26,"Where is ""8830""?"',
      "#2D,#2B,r2,,",
    ]);
  });
  it("names and sizes the file", () => {
    expect(csvFileName("#2 Five ways · v2", new Date(2026, 9, 15))).toBe(
      "2-five-ways-v2-2026-10-15.csv",
    );
    expect(fileSizeText(812)).toBe("812 B");
    expect(fileSizeText(4300)).toBe("4.2 KB");
    expect(fileSizeText(1.25 * 1024 * 1024)).toBe("1.3 MB");
  });
});
