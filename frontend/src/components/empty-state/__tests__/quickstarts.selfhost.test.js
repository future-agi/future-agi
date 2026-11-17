import { describe, expect, it, vi } from "vitest";

import {
  QUICKSTART_DATASET,
  QUICKSTART_PROMPT,
  quickstartTracing,
} from "../quickstarts";

// A self-hosted build's API host. vi.mock is hoisted above the imports, so the
// literal is repeated in the factory.
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "https://fa.example.test",
}));

const code = (quickstart) => quickstart.snippets[0].code;

// W11-QS-TRACING-SELFHOST: the tracing SDK sends to Future AGI cloud unless it
// is given a collector (README.md, "Self-hosted tracing"). The frontend's
// config has no collector address to print, so the snippet names the variable
// to set and the README's example address, as a comment: the code still runs
// as pasted on Future AGI cloud.
const SELF_HOST_NOTE =
  '# Self-hosted? Also set os.environ["FI_BASE_URL"] to your collector, for example "http://localhost:4318", or traces and both keys go to Future AGI cloud';

describe("tracing quickstart on a self-hosted instance", () => {
  it("says to set the instance's own collector before the tracer starts", () => {
    const text = code(quickstartTracing("my-project"));
    const lines = text.split("\n");
    const note = lines.indexOf(SELF_HOST_NOTE);
    expect(note).toBeGreaterThan(-1);
    // Beside the keys, which go to the same collector.
    expect(lines[note - 1]).toBe(
      'os.environ["FI_SECRET_KEY"] = "your-secret-key"',
    );
    expect(note).toBeLessThan(
      lines.indexOf("from fi_instrumentation import FITracer, register"),
    );
  });

  it("never points tracing at this instance's API", () => {
    const text = code(quickstartTracing("my-project"));
    expect(text).not.toContain("fa.example.test");
    expect(text.match(/^os\.environ\[/gm)).toHaveLength(2);
  });
});

// W11-QS-FI-BASE-URL-MEANINGS: the README sets FI_BASE_URL to the collector
// for tracing, and the futureagi SDK snippets set it to this instance's API.
// The SDK snippets' base line says which one it is.
const SDK_BASE_LINE =
  'os.environ["FI_BASE_URL"] = "https://fa.example.test"  # This instance\'s API (tracing uses its collector address instead)';

describe("futureagi SDK quickstarts name what their FI_BASE_URL is", () => {
  it.each([
    ["dataset", QUICKSTART_DATASET],
    ["prompt", QUICKSTART_PROMPT],
  ])("%s: the base line says it is this instance's API", (_, quickstart) => {
    const lines = code(quickstart).split("\n");
    expect(lines.filter((line) => line.includes("FI_BASE_URL"))).toEqual([
      SDK_BASE_LINE,
    ]);
  });
});
