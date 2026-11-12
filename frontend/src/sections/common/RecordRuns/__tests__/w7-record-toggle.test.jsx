/* eslint-disable react/prop-types */
// W7: the Review step's `Record on the
// leaderboard` is on and not changeable, with one line that says so, and no
// screen says a run is deleted 24 hours after it starts (the server has no
// `record` field and deletes no run).
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { useForm } from "react-hook-form";
import { palette } from "src/theme/palette";
import RecordToggle from "../RecordToggle";
import {
  NOT_RECORDED_HELP,
  NOT_RECORDED_SUMMARY,
  RECORD_HELP,
  notRecordedRowText,
  recordFieldsOf,
} from "../recordModel";

const LINE =
  "Keeping a run off the leaderboard is not available in this release.";
const DELETION = /deleted|24 hours|hours after/i;

// The Review step renders the switch inside the wizard's form. Whatever the
// form holds (nothing, or a stale `record: false`) it shows the same.
const Harness = ({ defaultValues }) => {
  const { control } = useForm({ defaultValues });
  return <RecordToggle control={control} />;
};

const ui = (defaultValues) => (
  <ThemeProvider theme={createTheme({ palette: palette("light") })}>
    <Harness defaultValues={defaultValues} />
  </ThemeProvider>
);

describe.each([
  ["no record value", {}],
  ["a stale record: false", { record: false }],
  ["record: true", { record: true }],
])("Record on the leaderboard, form with %s", (_label, defaultValues) => {
  it("shows the switch on and not changeable, still labelled", () => {
    render(ui(defaultValues));
    const sw = screen.getByTestId("review-record-switch");
    expect(sw).toBeChecked();
    expect(sw).toBeDisabled();
    expect(screen.getByText("Record on the leaderboard")).toBeInTheDocument();
    expect(screen.getByTestId("review-record")).toBeVisible();
  });

  it("says in one line that keeping a run off is not available", () => {
    render(ui(defaultValues));
    const line = screen.getByText(LINE);
    expect(screen.getByTestId("review-record-switch")).toHaveAttribute(
      "aria-describedby",
      line.id,
    );
  });

  it("says nothing about deleting the run", () => {
    render(ui(defaultValues));
    expect(screen.getByTestId("review-record").textContent).not.toMatch(
      DELETION,
    );
  });
});

describe("Record copy (W7)", () => {
  it("the switch's line is the ruling's exact sentence", () => {
    expect(RECORD_HELP).toBe(LINE);
  });

  it("no record text claims a deletion", () => {
    const start = new Date(2026, 9, 23, 14, 5).getTime();
    const run = {
      run_id: "r9a",
      label: "#9A",
      name: "Scratch",
      ...recordFieldsOf({ record: false }, start),
    };
    [
      RECORD_HELP,
      NOT_RECORDED_HELP,
      NOT_RECORDED_SUMMARY,
      notRecordedRowText(run, start + 60000),
    ].forEach((text) => expect(text).not.toMatch(DELETION));
    expect(notRecordedRowText(run, start + 60000)).toBe("#9A · Scratch");
  });
});
