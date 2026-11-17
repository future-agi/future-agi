// Every place that explains the Success rule shows the same words: the
// list's Success cell and its column header, the glossary's Success rate
// entry and the Summary's All runs table. One constant holds them.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { fmtRate } from "src/sections/common/format/fmt";
import { glossaryEntry } from "src/sections/common/ux/glossary";
import { successText } from "src/sections/experiment-detail/ExperimentSummary/Analysis/allRunsModel";
import SummaryHeader from "../SummaryHeader";
import gridSource from "../LeaderboardGrid.jsx?raw";
import cellsSource from "../LeaderboardSummaryCells.jsx?raw";
import glossarySource from "src/sections/common/ux/glossary.js?raw";

// The tooltip's text is drawn next to what it wraps, so a test can read it.
vi.mock("src/components/tooltip", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ title, children }) =>
      createElement(
        "span",
        null,
        children,
        createElement("span", { "data-testid": "tip" }, title),
      ),
  };
});

const RULE =
  "Success rate = passed tries ÷ counted tries (pass@1). A try counts when it has a Pass/fail result or it errored; errored tries count as fails. Tries that didn't run or have no Pass/fail result are not counted.";

const ruleText = async () =>
  (await import("src/sections/common/Runs/successRule")).SUCCESS_RULE_TEXT;

// The Success column's entry in the grid's column table.
const successColumn = () => {
  const start = gridSource.indexOf("  success_rate: {");
  const end = gridSource.indexOf("  cost_per_task: {", start);
  return gridSource.slice(start, end);
};

describe("the Success rule's words", () => {
  it("are held by one constant", async () => {
    expect(await ruleText()).toBe(RULE);
  });

  it("are the glossary's Success rate entry", () => {
    expect(glossaryEntry("successRate").short).toBe(RULE);
    expect(glossarySource).not.toContain("Success rate = passed tries");
  });

  it("are the glossary's pass@1 entry, the same measure", () => {
    expect(glossaryEntry("passAt1").short).toBe(RULE);
    expect(glossaryEntry("passAt1").label).toBe("pass@1");
  });

  it("are the list's Success header tip, with no glossary line before it", async () => {
    const column = successColumn();
    expect(column).toContain("tip: SUCCESS_RULE_TEXT");
    expect(column).not.toContain("termKey");
    render(
      <SummaryHeader displayName="Success (pass@1)" tip={await ruleText()} />,
    );
    expect(screen.getByTestId("tip").textContent).toBe(RULE);
  });

  it("follow this run's numbers in the Summary's All runs tip, in tries", () => {
    const run = {
      success_rate: 59 / 90,
      success_pass: 59,
      success_n: 90,
      success_tasks: 30,
    };
    expect(successText(run).tip).toBe(
      `${fmtRate(59, 90)}: 59 of 90 tries passed across 30 tasks. ${RULE}`,
    );
  });

  it("are not kept as a second copy in the Success cell", () => {
    expect(cellsSource).toContain("SUCCESS_RULE_TEXT");
    expect(cellsSource).not.toContain("Success rate = passed tries");
  });
});
