/* eslint-disable react/prop-types */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";

// Reverses eval-score filters; irrelevant to dates and the default (empty)
// filter list never reaches it.
vi.mock("src/hooks/use-reverse-eval-filters", () => ({
  default: (filters) => filters,
}));

import { useLLMTracingFilters } from "../useLLMTracingFilters";

const NOW = new Date(2026, 9, 15, 11, 49, 0);
const STALE_TODAY = {
  dateFilter: ["2026-10-01 00:00:00", "2026-10-02 00:00:00"],
  dateOption: "Today",
};
const FRESH_TODAY_BOUNDS = ["2026-10-15 00:00:00", "2026-10-16 00:00:00"];
const FRESH_TODAY = { dateFilter: FRESH_TODAY_BOUNDS, dateOption: "Today" };
const CUSTOM = {
  dateFilter: ["2025-10-15 00:00:00", "2025-10-23 00:00:00"],
  dateOption: "Custom",
};
const DEFAULT_DATE = FRESH_TODAY;
const NO_COLUMNS = [];
const noExtraProperties = () => ({});

const wrapper = ({ children }) => <BrowserRouter>{children}</BrowserRouter>;
const dateParam = (value) => encodeURIComponent(JSON.stringify(value));

const openWithUrl = (search, key = "primaryTraceDateFilter") => {
  window.history.replaceState({}, "", `/test${search}`);
  return renderHook(
    () =>
      useLLMTracingFilters(
        NO_COLUMNS,
        DEFAULT_DATE,
        "primaryTraceFilter",
        key,
        NO_COLUMNS,
        noExtraProperties,
      ),
    { wrapper },
  );
};

const urlDate = (key = "primaryTraceDateFilter") => {
  const raw = new URLSearchParams(window.location.search).get(key);
  return raw === null ? null : JSON.parse(raw);
};

const isoBounds = (result) =>
  result.current.validatedFilters.at(-1).filter_config.filter_value;

describe("useLLMTracingFilters preset dates (TH-8634)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    "primaryTraceDateFilter",
    "primarySpanDateFilter",
    "compareTraceDateFilter",
    "compareSpansDateFilter",
    "sessionDateFilter",
    "userDateFilter",
  ])(
    "a stale Today link in %s loads today's dates and the URL is rewritten",
    async (key) => {
      const { result } = openWithUrl(`?${key}=${dateParam(STALE_TODAY)}`, key);
      expect(result.current.dateFilter.dateFilter).toEqual(FRESH_TODAY_BOUNDS);
      await waitFor(() => expect(urlDate(key)).toEqual(FRESH_TODAY));
    },
  );

  it("queries fresh dates from the first render", () => {
    const { result } = openWithUrl(
      `?primaryTraceDateFilter=${dateParam(STALE_TODAY)}`,
    );
    expect(isoBounds(result)).toEqual(
      FRESH_TODAY_BOUNDS.map((d) => new Date(d).toISOString()),
    );
  });

  it("rewrites without adding a history entry, and settles", async () => {
    window.history.replaceState({}, "", "/test");
    const lengthBefore = window.history.length;
    const { result } = openWithUrl(
      `?primaryTraceDateFilter=${dateParam(STALE_TODAY)}`,
    );
    await waitFor(() => expect(urlDate()).toEqual(FRESH_TODAY));
    const settled = result.current.dateFilter;
    await act(async () => {});
    expect(result.current.dateFilter).toBe(settled);
    expect(window.history.length).toBe(lengthBefore);
  });

  it("leaves a Custom link and its URL untouched", async () => {
    const search = `?primaryTraceDateFilter=${dateParam(CUSTOM)}`;
    const { result } = openWithUrl(search);
    await act(async () => {});
    expect(result.current.dateFilter).toEqual(CUSTOM);
    expect(window.location.search).toBe(search);
  });

  it("does not add a date to a URL that has none", async () => {
    const { result } = openWithUrl("");
    await act(async () => {});
    expect(result.current.dateFilter).toEqual(DEFAULT_DATE);
    expect(urlDate()).toBeNull();
  });

  it("keeps the URL clean when a page's default was computed before the day changed", async () => {
    window.history.replaceState({}, "", "/test");
    const yesterdaysDefault = {
      dateFilter: ["2026-10-14 00:00:00", "2026-10-15 00:00:00"],
      dateOption: "Today",
    };
    const { result } = renderHook(
      () =>
        useLLMTracingFilters(
          NO_COLUMNS,
          yesterdaysDefault,
          "primaryTraceFilter",
          "primaryTraceDateFilter",
          NO_COLUMNS,
          noExtraProperties,
        ),
      { wrapper },
    );
    await act(async () => {});
    expect(result.current.dateFilter).toEqual(FRESH_TODAY);
    expect(urlDate()).toBeNull();
  });

  it("fills in dates for a preset link without any", async () => {
    const { result } = openWithUrl(
      `?primaryTraceDateFilter=${dateParam({ dateOption: "Today" })}`,
    );
    expect(result.current.dateFilter.dateFilter).toEqual(FRESH_TODAY_BOUNDS);
    await waitFor(() => expect(urlDate()).toEqual(FRESH_TODAY));
  });

  it("re-resolves a stale preset applied after load (saved view opened)", async () => {
    const { result } = openWithUrl("");
    act(() => result.current.setDateFilter(STALE_TODAY));
    expect(result.current.dateFilter.dateFilter).toEqual(FRESH_TODAY_BOUNDS);
    await waitFor(() => expect(urlDate()).toEqual(FRESH_TODAY));
  });
});
