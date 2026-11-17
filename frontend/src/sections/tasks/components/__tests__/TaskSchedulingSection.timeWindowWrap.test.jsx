import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { useForm } from "react-hook-form";
import { render, screen, within } from "src/utils/test-utils";
import { TIME_PERIOD_OPTIONS } from "src/sections/projects/timeWindowPresets";
import TaskSchedulingSection from "../TaskSchedulingSection";

// Tasks > New task > Scheduling > Time window. At a 1024 px window the form
// column is 412 px wide, and the time-window chips (Custom ... 12M) sat in one
// fixed 28 px row with a 556 px floor. The row ran past the column: 30D was
// cut and 3M, 6M and 12M could not be reached. At 1280 12M lost its right
// edge. The row must wrap its chips inside the column and keep all of them.
//
// jsdom applies style rules in sheet order and ignores selector specificity,
// so the row's own min-width and height still win in getComputedStyle. Those
// two are read from the rule the fix adds; flex-wrap and max-width are set by
// nothing else on the row, so they are checked as computed styles.

const Harness = ({ isEdit }) => {
  const form = useForm({
    defaultValues: {
      runType: "historical",
      spansLimit: 1000,
      samplingRate: 100,
      datePreset: "12M",
      startDate: "2025-11-11 00:00:00",
      endDate: "2026-11-11 00:00:00",
    },
  });
  return <TaskSchedulingSection control={form.control} isEdit={isEdit} />;
};
Harness.propTypes = { isEdit: PropTypes.bool.isRequired };

const chipRow = () => screen.getByRole("button", { name: "12M" }).parentElement;

// Every style rule in the document whose selector matches the element.
const rulesMatching = (el) => {
  const found = [];
  const visit = (rules) => {
    for (const rule of Array.from(rules ?? [])) {
      if (rule.cssRules) visit(rule.cssRules);
      if (!rule.selectorText || !rule.style) continue;
      try {
        if (el.matches(rule.selectorText)) found.push(rule);
      } catch {
        // A selector jsdom cannot parse is not the rule under test.
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules);
  return found;
};

describe.each([
  ["create", false],
  ["edit", true],
])("Task time-window chips at narrow widths (%s)", (_path, isEdit) => {
  it("wraps the chip row inside the form column", () => {
    render(<Harness isEdit={isEdit} />);

    expect(chipRow()).toHaveStyle({ flexWrap: "wrap", maxWidth: "100%" });
  });

  it("drops the row's fixed floor and height so wrapped chips stay inside its border", () => {
    render(<Harness isEdit={isEdit} />);

    const wrapRule = rulesMatching(chipRow()).find(
      (rule) => rule.style.getPropertyValue("flex-wrap") === "wrap",
    );
    expect(wrapRule).toBeDefined();
    expect(["0", "0px"]).toContain(
      wrapRule.style.getPropertyValue("min-width"),
    );
    expect(wrapRule.style.getPropertyValue("height")).toBe("auto");
  });

  it("keeps every time-window chip in the row", () => {
    render(<Harness isEdit={isEdit} />);

    const row = chipRow();
    for (const label of [
      "Custom",
      ...TIME_PERIOD_OPTIONS.map((option) => option.title),
    ]) {
      expect(
        within(row).getByRole("button", { name: label }),
      ).toBeInTheDocument();
    }
  });
});
