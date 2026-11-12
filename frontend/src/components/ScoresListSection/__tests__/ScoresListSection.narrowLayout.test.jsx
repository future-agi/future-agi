import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import ScoresListSection from "../ScoresListSection";

// In the trace drawer's narrow span pane, the Annotations table keeps its
// headers on one line and its text columns at a minimum width; a table wider
// than its pane scrolls sideways and fades the edge that has more columns
// past it.
// jsdom has no layout. The tests pin the styles and attributes the fix sets;
// the fade test fakes the scroller's geometry (scrollWidth, clientWidth,
// scrollLeft) on the element itself and never reads a size from jsdom.

const score = vi.hoisted(() => ({
  id: "score-1",
  label_id: "label-1",
  source_type: "observation_span",
  source_id: "span-1",
  label_name: "quality-note-1024-mwbk8a5o",
  label_type: "text",
  value: { text: "Answer was correct and polite." },
  annotator_name: "E2E fl2x0",
  score_source: "human",
  notes: "",
  updated_at: "2026-11-11T03:29:00Z",
}));

vi.mock("src/api/scores/scores", async (importOriginal) => ({
  ...(await importOriginal()),
  useScoresForSource: (sourceType) => ({
    isLoading: false,
    data: sourceType === "observation_span" ? [score] : [],
  }),
  useSpanNotes: () => ({ data: [] }),
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useQueueItemsForSource: () => ({ data: [] }),
  }),
);

for (const name of ["solar:user-linear", "solar:plug-circle-linear"]) {
  registerOfflineIcon(name, bundled[name]);
}

const renderTable = () =>
  render(
    <ScoresListSection
      sourceType="observation_span"
      sourceId="span-1"
      secondarySourceType="trace"
      secondarySourceId="trace-1"
      projectId="project-1"
      title=""
    />,
  );

// Stands in for the browser's layout: the sizes a pane narrower than the
// table would report, then the scroll event that makes the table re-check.
const fakeScroll = (scroller, geometry) => {
  for (const [key, value] of Object.entries(geometry)) {
    Object.defineProperty(scroller, key, { configurable: true, value });
  }
  fireEvent.scroll(scroller);
};

describe("ScoresListSection in a narrow drawer pane", () => {
  it("keeps every header on one line", () => {
    renderTable();

    for (const name of [
      "Label",
      "Value",
      "Annotator",
      "Updated At",
      "Label Notes",
    ]) {
      expect(screen.getByRole("columnheader", { name })).toHaveStyle({
        whiteSpace: "nowrap",
      });
    }
  });

  it("gives the label, value, date and notes columns a floor and keeps every cell in the row", () => {
    renderTable();

    for (const [name, floor] of [
      ["Label", "136px"],
      ["Value", "150px"],
      ["Updated At", "96px"],
      ["Label Notes", "110px"],
    ]) {
      expect(screen.getByRole("columnheader", { name })).toHaveStyle({
        minWidth: floor,
      });
    }

    // Nothing is dropped to make room.
    const row = screen.getByText("quality-note-1024-mwbk8a5o").closest("tr");
    const cells = within(row).getAllByRole("cell");
    expect(cells).toHaveLength(5);
    expect(cells[0]).toHaveTextContent("quality-note-1024-mwbk8a5o");
    expect(cells[1]).toHaveTextContent("Answer was correct and polite.");
    expect(cells[2]).toHaveTextContent("E2E fl2x0");
    expect(cells[3]).not.toBeEmptyDOMElement();
    expect(cells[4]).toHaveTextContent("—");
  });

  it("fades the edge of a table wider than its pane while columns sit past it", () => {
    renderTable();

    const scroller = screen.getByRole("table").parentElement;
    // The table still scrolls sideways in its container (MUI TableContainer)
    // and the card's border sits on a wrapper, so a fade leaves it whole.
    expect(scroller).toHaveStyle({ overflowX: "auto" });
    expect(scroller.parentElement).toHaveStyle({
      borderRadius: "8px",
      overflow: "hidden",
    });
    expect(scroller).not.toHaveAttribute("data-hidden-edges");

    // At the start of a 590 px table in a 326 px pane: more on the right.
    fakeScroll(scroller, {
      scrollWidth: 590,
      clientWidth: 326,
      scrollLeft: 0,
    });
    expect(scroller).toHaveAttribute("data-hidden-edges", "end");

    // Part way along: columns past both edges.
    fakeScroll(scroller, { scrollLeft: 120 });
    expect(scroller).toHaveAttribute("data-hidden-edges", "both");

    // Scrolled to the end: only the start has columns past it.
    fakeScroll(scroller, { scrollLeft: 264 });
    expect(scroller).toHaveAttribute("data-hidden-edges", "start");

    // A pane wide enough for the whole table: no fade.
    fakeScroll(scroller, {
      scrollWidth: 600,
      clientWidth: 600,
      scrollLeft: 0,
    });
    expect(scroller).not.toHaveAttribute("data-hidden-edges");
  });
});
