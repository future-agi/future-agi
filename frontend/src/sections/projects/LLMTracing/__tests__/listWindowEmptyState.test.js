import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { observePresetDateFilter } from "src/sections/projects/timeWindowPresets";
import {
  buildListWindow,
  describeListWindow,
  getListWindowBounds,
  getProjectLastTrace,
  isOutsideListWindow,
} from "../listWindowState";

// Which lists may show the out-of-window empty state, and when.
// Instants sit at 12:00 UTC, so the dates hold for UTC offsets under 12 h.
const NOW = new Date("2026-11-11T12:00:00Z");

const pastWeek = () => ({
  dateOption: "7D",
  dateFilter: observePresetDateFilter("7D"),
});

const unfiltered = () => ({
  projectId: "project-1",
  projectName: "Support bot",
  dateFilter: pastWeek(),
  setDateFilter: vi.fn(),
  validatedFilters: [{ column_id: "created_at" }],
  metricFilters: [],
  hasEvalFilter: false,
});

const lastTraceAt = (iso) =>
  getProjectLastTrace({ last_active: iso, activity_query_complete: true });

describe("listWindowEmptyState helpers", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gives a window only to an unfiltered project list", () => {
    expect(buildListWindow(unfiltered())).toMatchObject({
      projectId: "project-1",
      projectName: "Support bot",
    });
    // A filter-panel row, the errors / non-annotated toggles and the eval
    // filter make an empty page a no-match, so those keep their state.
    expect(
      buildListWindow({
        ...unfiltered(),
        validatedFilters: [
          { column_id: "status" },
          { column_id: "created_at" },
        ],
      }),
    ).toBeNull();
    expect(
      buildListWindow({ ...unfiltered(), metricFilters: [{ column_id: "x" }] }),
    ).toBeNull();
    expect(
      buildListWindow({ ...unfiltered(), hasEvalFilter: true }),
    ).toBeNull();
    // User mode has no project; a project still loading has no name.
    expect(buildListWindow({ ...unfiltered(), projectId: null })).toBeNull();
    expect(
      buildListWindow({ ...unfiltered(), projectName: undefined }),
    ).toBeNull();
  });

  it("counts only a last trace hour wholly outside the window", () => {
    const bounds = getListWindowBounds(pastWeek());
    expect(
      isOutsideListWindow(lastTraceAt("2026-11-01T12:00:00Z"), bounds),
    ).toBe(true);
    // The hour that opens the window, and the last full hour, are inside it.
    expect(
      isOutsideListWindow(lastTraceAt(bounds.start.toISOString()), bounds),
    ).toBe(false);
    expect(
      isOutsideListWindow(lastTraceAt("2026-11-11T11:00:00Z"), bounds),
    ).toBe(false);
    // Nothing traced in the rollup's 90 days, or activity that was not read.
    expect(lastTraceAt(null)).toBeNull();
    expect(
      getProjectLastTrace({
        last_active: "2026-11-01T12:00:00Z",
        activity_query_complete: false,
      }),
    ).toBeNull();
  });

  it("adds times when the window is a single day", () => {
    const bounds = getListWindowBounds({
      dateOption: "Today",
      dateFilter: observePresetDateFilter("Today"),
    });
    expect(describeListWindow(bounds, null)).toBe(
      "between 11 Nov 2026 12:00 AM and 11:59 PM",
    );
  });
});
