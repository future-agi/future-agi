import { describe, expect, it } from "vitest";
import { LLM_TABS } from "../../../../sections/projects/LLMTracing/common";
import { importSelection } from "../importSelection";

describe("a trace-drawer import's selection", () => {
  it("imports the selected traces as a trace source on the trace tab", () => {
    expect(
      importSelection({
        currentTab: LLM_TABS.TRACE,
        selectedTraces: ["T1", "T2"],
        selectedSpans: ["S1"],
        selectedNode: "S9",
      }),
    ).toEqual({ source_kind: "trace", trace_ids: ["T1", "T2"] });
  });

  it("imports the selected spans as a span source on any other tab", () => {
    expect(
      importSelection({
        currentTab: LLM_TABS.SPAN,
        selectedTraces: ["T1"],
        selectedSpans: ["S1", "S2"],
      }),
    ).toEqual({ source_kind: "span", span_ids: ["S1", "S2"] });
  });

  it("imports the one open span when the drawer has one", () => {
    expect(
      importSelection({
        currentTab: LLM_TABS.SPAN,
        selectedSpans: ["S1", "S2"],
        selectedNode: "S9",
      }),
    ).toEqual({ source_kind: "span", span_ids: ["S9"] });
  });
});
