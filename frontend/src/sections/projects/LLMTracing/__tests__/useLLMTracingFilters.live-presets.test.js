import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import {
  observePresetDateFilter,
  presetToRange,
} from "../../timeWindowPresets";
import { OBSERVE_LIST_REFRESH_EVENT } from "../../observeEvents";
import { useLLMTracingFilters } from "../useLLMTracingFilters";

vi.mock("src/hooks/use-reverse-eval-filters", () => ({
  default: (filters) => filters,
}));

const HOUR_MS = 60 * 60 * 1000;
const EMPTY = [];
const noExtraProperties = () => ({});
const DATE_KEY = "primaryTraceDateFilter";
const ROLLING = ["7D", "30D", "3M", "6M", "12M"];
const REFRESH_EVENTS = ["observe-refresh", OBSERVE_LIST_REFRESH_EVENT];
const CUSTOM = {
  dateOption: "Custom",
  dateFilter: ["2026-10-01T10:11:12.123Z", "2026-10-02T13:14:15.678Z"],
};

const wrapper = ({ children }) =>
  React.createElement(BrowserRouter, null, children);
const strictWrapper = ({ children }) =>
  React.createElement(
    React.StrictMode,
    null,
    React.createElement(wrapper, null, children),
  );
const preset = (dateOption, now = new Date()) => ({
  dateOption,
  dateFilter: observePresetDateFilter(dateOption, now),
});
const urlDate = () => {
  const value = new URLSearchParams(window.location.search).get(DATE_KEY);
  return value === null ? null : JSON.parse(value);
};
const open = (defaultDate, { linked = false, strict = false } = {}) => {
  const search = new URLSearchParams({ keep: "unchanged" });
  if (linked) search.set(DATE_KEY, JSON.stringify(defaultDate));
  window.history.replaceState({}, "", `/test?${search}`);
  return renderHook(
    () =>
      useLLMTracingFilters(
        EMPTY,
        defaultDate,
        "primaryTraceFilter",
        DATE_KEY,
        EMPTY,
        noExtraProperties,
      ),
    { wrapper: strict ? strictWrapper : wrapper },
  );
};
const refresh = (eventName) => {
  act(() => window.dispatchEvent(new CustomEvent(eventName)));
};
const expectWindow = (result, expected) => {
  expect(result.current.dateFilter).toEqual(expected);
  expect(result.current.validatedFilters).toEqual([
    {
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: expected.dateFilter.map((value) =>
          new Date(value).toISOString(),
        ),
      },
    },
  ]);
};

describe("useLLMTracingFilters live presets", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(new Date(2026, 9, 15, 11, 20, 0));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
    window.history.replaceState({}, "", "/test");
  });

  it.each(["Today", "Yesterday", ...ROLLING])(
    "rolls %s across two local midnights without a refresh event",
    (option) => {
      vi.setSystemTime(new Date(2026, 9, 15, 23, 59, 59, 999));
      const { result } = open(preset(option));
      const previous = result.current.validatedFilters;

      act(() => vi.advanceTimersByTime(1));
      expectWindow(result, preset(option));
      expect(result.current.validatedFilters).not.toBe(previous);
      if (option === "Today") {
        expect(result.current.dateFilter.dateFilter).toEqual([
          "2026-10-16 00:00:00",
          "2026-10-17 00:00:00",
        ]);
      }
      if (option === "Yesterday") {
        expect(result.current.dateFilter.dateFilter).toEqual([
          "2026-10-15 00:00:00",
          "2026-10-16 00:00:00",
        ]);
      }
      expect(urlDate()).toBeNull();

      const firstDay = result.current.validatedFilters;
      const nextMidnight = new Date(2026, 9, 17).getTime();
      act(() => vi.advanceTimersByTime(nextMidnight - Date.now()));
      expectWindow(result, preset(option));
      expect(result.current.validatedFilters).not.toBe(firstDay);
      expect(urlDate()).toBeNull();
    },
  );

  it.each(ROLLING)(
    "advances %s at consecutive rolling-start hour boundaries",
    (option) => {
      const now = new Date("2026-10-15T11:20:00.000Z");
      const start = presetToRange(option, now)[0].getTime();
      // Subtracting calendar days/months can cross a fractional DST offset.
      const untilStartHour = HOUR_MS - (start % HOUR_MS);
      vi.setSystemTime(new Date(now.getTime() + untilStartHour - 1));
      const { result } = open(preset(option));
      const previous = result.current.validatedFilters;
      const previousStart = new Date(
        result.current.dateFilter.dateFilter[0],
      ).getTime();

      act(() => vi.advanceTimersByTime(1));
      expectWindow(result, preset(option));
      expect(result.current.validatedFilters).not.toBe(previous);
      expect(new Date(result.current.dateFilter.dateFilter[0]).getTime()).toBe(
        previousStart + HOUR_MS,
      );

      act(() => vi.advanceTimersByTime(HOUR_MS));
      expectWindow(result, preset(option));
      expect(new Date(result.current.dateFilter.dateFilter[0]).getTime()).toBe(
        previousStart + 2 * HOUR_MS,
      );
    },
  );

  it.each([
    ["Today", "observe-refresh"],
    ["Today", OBSERVE_LIST_REFRESH_EVENT],
    ["7D", "observe-refresh"],
    ["7D", OBSERVE_LIST_REFRESH_EVENT],
  ])(
    "catches up %s on %s when boundary timers have not run",
    (option, eventName) => {
      const { result } = open(preset(option));
      const previous = result.current.validatedFilters;
      const previousEnd = new Date(
        result.current.dateFilter.dateFilter[1],
      ).getTime();

      // Simulate a suspended tab/clock jump without firing the scheduled timeout.
      vi.setSystemTime(new Date(2026, 9, 18, 12, 0, 0));
      const newTraceStart = new Date(2026, 9, 18, 11, 0, 0).getTime();
      expect(newTraceStart).toBeGreaterThan(previousEnd);
      refresh(eventName);

      expectWindow(result, preset(option));
      expect(result.current.validatedFilters).not.toBe(previous);
      const [start, end] =
        result.current.validatedFilters[0].filter_config.filter_value;
      expect(newTraceStart).toBeGreaterThanOrEqual(new Date(start).getTime());
      expect(newTraceStart).toBeLessThan(new Date(end).getTime());
      expect(urlDate()).toBeNull();
    },
  );

  it("rewrites a linked preset at rollover without adding history or losing other params", () => {
    vi.setSystemTime(new Date(2026, 9, 15, 23, 59, 59, 999));
    const { result } = open(preset("Today"), { linked: true });
    const historyLength = window.history.length;

    act(() => vi.advanceTimersByTime(1));
    expectWindow(result, preset("Today"));
    expect(urlDate()).toEqual(preset("Today"));
    expect(window.history.length).toBe(historyLength);
    expect(new URLSearchParams(window.location.search).get("keep")).toBe(
      "unchanged",
    );
  });

  it("keeps resolved references stable on same-window refreshes of a stale default", () => {
    const { result } = open(preset("7D", new Date(2026, 9, 14, 11, 20, 0)));
    const resolved = result.current.dateFilter;
    const filters = result.current.validatedFilters;
    const search = window.location.search;

    act(() => vi.advanceTimersByTime(10000));
    REFRESH_EVENTS.forEach(refresh);
    expect(result.current.dateFilter).toBe(resolved);
    expect(result.current.validatedFilters).toBe(filters);
    expect(window.location.search).toBe(search);
    expect(urlDate()).toBeNull();
  });

  it("rearms the boundary when switching from Today to a rolling preset", () => {
    const { result } = open(preset("Today"));
    act(() => result.current.setDateFilter(preset("7D")));
    const previous = result.current.validatedFilters;
    const start = presetToRange("7D")[0].getTime();
    const untilStartHour = HOUR_MS - (start % HOUR_MS);

    act(() => vi.advanceTimersByTime(untilStartHour));
    expectWindow(result, preset("7D"));
    expect(result.current.validatedFilters).not.toBe(previous);
    expect(urlDate()).toEqual(preset("7D"));
  });

  it("preserves a Custom selection after a preset timer was armed", () => {
    vi.setSystemTime(new Date(2026, 9, 15, 23, 59, 59, 999));
    const { result } = open(preset("Today"));
    act(() => result.current.setDateFilter(CUSTOM));
    const filters = result.current.validatedFilters;
    const search = window.location.search;

    act(() => vi.advanceTimersByTime(2 * 24 * HOUR_MS));
    REFRESH_EVENTS.forEach(refresh);
    expectWindow(result, CUSTOM);
    expect(result.current.dateFilter).toBe(CUSTOM);
    expect(result.current.validatedFilters).toBe(filters);
    expect(window.location.search).toBe(search);
  });

  it.each(["Custom", "30 mins", "6 hrs", "unsupported"])(
    "does not move explicitly stored bounds for %s",
    (dateOption) => {
      const selected = { ...CUSTOM, dateOption };
      const { result } = open(selected, { linked: true });
      const filters = result.current.validatedFilters;
      const search = window.location.search;

      act(() => vi.advanceTimersByTime(2 * 24 * HOUR_MS));
      REFRESH_EVENTS.forEach(refresh);
      expectWindow(result, selected);
      expect(result.current.validatedFilters).toBe(filters);
      expect(window.location.search).toBe(search);
    },
  );

  it.each([
    [2026, 2, 7],
    [2026, 9, 31],
  ])(
    "uses local calendar midnights around DST dates %i-%i-%i",
    (year, month, day) => {
      vi.setSystemTime(new Date(year, month, day, 23, 59, 59, 999));
      const { result } = open(preset("Today"));
      act(() => vi.advanceTimersByTime(1));
      expectWindow(result, preset("Today"));
      const filters = result.current.validatedFilters;
      const nextMidnight = new Date(year, month, day + 2).getTime();

      act(() => vi.advanceTimersByTime(nextMidnight - Date.now() - 1));
      expect(result.current.validatedFilters).toBe(filters);
      act(() => vi.advanceTimersByTime(1));
      expectWindow(result, preset("Today"));
      expect(result.current.validatedFilters).not.toBe(filters);
    },
  );

  it("rechecks rolling presets across local-hour daylight transitions", () => {
    // This date jumps from 01:59:59 to 02:30 in Australia/Lord_Howe.
    vi.setSystemTime(new Date(2026, 9, 4, 1, 59, 59, 999));
    const { result } = open(preset("7D"));
    act(() => vi.advanceTimersByTime(1));
    expectWindow(result, preset("7D"));

    const nextLocalHour = new Date(2026, 9, 4, 3).getTime();
    act(() => vi.advanceTimersByTime(nextLocalHour - Date.now()));
    expectWindow(result, preset("7D"));
  });

  it("cleans up both refresh listeners and the timer under StrictMode", () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    const pendingBefore = vi.getTimerCount();
    const { unmount } = open(preset("Today"), { strict: true });
    const registrations = add.mock.calls.filter(([eventName]) =>
      REFRESH_EVENTS.includes(eventName),
    );
    expect(new Set(registrations.map(([eventName]) => eventName))).toEqual(
      new Set(REFRESH_EVENTS),
    );
    expect(vi.getTimerCount()).toBe(pendingBefore + 1);

    unmount();
    expect(vi.getTimerCount()).toBe(pendingBefore);
    registrations.forEach(([eventName, listener]) => {
      expect(remove).toHaveBeenCalledWith(eventName, listener);
    });
  });
});
