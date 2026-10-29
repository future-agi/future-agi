import { describe, expect, it } from "vitest";

import { tracesToCsv } from "../exportTracesCsv";

const HEADER =
  "trace_id,timestamp,model,score,latency_ms,turns,input_tokens," +
  "output_tokens,input,output,judge_reason,failure_evidence";

const trace = (overrides = {}) => ({
  id: "t1",
  status: "fail",
  timestamp: "2026-10-20T10:00:00Z",
  summary: {
    eval_score: 0.4,
    latency_ms: 1200,
    turns: 3,
    model: "gpt-4o",
    input_tokens: 500,
    output_tokens: 80,
  },
  evidence: {
    input: "Book a table for two",
    output: "Done",
    fail_reel: [],
    pass_reel: [{ label: "OK", text: "ignored" }],
    judge_reason: "Claimed success without calling the tool",
    score: 0.2,
  },
  ...overrides,
});

describe("tracesToCsv", () => {
  it("writes one header and one flat line per trace", () => {
    expect(tracesToCsv([trace()]).split("\r\n")).toEqual([
      HEADER,
      "t1,2026-10-20T10:00:00Z,gpt-4o,0.2,1200,3,500,80," +
        "Book a table for two,Done,Claimed success without calling the tool,",
    ]);
  });

  it("flattens string and highlighted-segment reel steps to labelled lines", () => {
    const csv = tracesToCsv([
      trace({
        evidence: {
          ...trace().evidence,
          fail_reel: [
            {
              label: "FINDING",
              text: [
                { t: "Agent " },
                { t: "claimed", hl: "error" },
                { t: " done" },
              ],
            },
            { label: "SYMPTOM", text: "No tool call" },
          ],
        },
      }),
    ]);

    expect(
      csv.endsWith(',"FINDING: Agent claimed done\nSYMPTOM: No tool call"'),
    ).toBe(true);
  });

  it("keeps rows whose trace data is unavailable", () => {
    const csv = tracesToCsv([
      trace({
        timestamp: null,
        summary: {},
        evidence: { input: null, output: null, fail_reel: [] },
      }),
    ]);

    expect(csv.split("\r\n")[1]).toBe("t1,,,,,,,,,,,");
  });

  it("quotes commas, quotes and newlines and leaves leading symbols as-is", () => {
    const csv = tracesToCsv([
      trace({
        evidence: {
          ...trace().evidence,
          input: '- step one, then "two"\n+1',
          output: "=SUM(A1)",
        },
      }),
    ]);

    expect(csv).toContain(',"- step one, then ""two""\n+1",=SUM(A1),');
  });

  it("writes only the header when there are no traces", () => {
    expect(tracesToCsv([])).toBe(HEADER);
  });
});
