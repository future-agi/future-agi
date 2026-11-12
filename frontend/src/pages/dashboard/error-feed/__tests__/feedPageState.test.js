import { describe, expect, it } from "vitest";
import { FEED_PAGE_STATE, deriveFeedPageState } from "../feedPageState";

const ok = (data) => ({ data, isError: false, isSuccess: true });
const pending = () => ({ data: undefined, isError: false, isSuccess: false });
const failed = () => ({ data: undefined, isError: true, isSuccess: false });
const page = (rows, total = rows.length) => ({ data: rows, total });

describe("deriveFeedPageState (TH-8209)", () => {
  it("is loading until both reads have a result", () => {
    expect(deriveFeedPageState({ catalog: pending(), feed: pending() })).toBe(
      FEED_PAGE_STATE.LOADING,
    );
    expect(deriveFeedPageState({ catalog: ok([]), feed: pending() })).toBe(
      FEED_PAGE_STATE.LOADING,
    );
    expect(
      deriveFeedPageState({ catalog: pending(), feed: ok(page([])) }),
    ).toBe(FEED_PAGE_STATE.LOADING);
  });

  it("reports an error whenever either read failed, even with an empty picker", () => {
    expect(deriveFeedPageState({ catalog: ok([]), feed: failed() })).toBe(
      FEED_PAGE_STATE.ERROR,
    );
    expect(deriveFeedPageState({ catalog: failed(), feed: ok(page([])) })).toBe(
      FEED_PAGE_STATE.ERROR,
    );
    expect(deriveFeedPageState({ catalog: pending(), feed: failed() })).toBe(
      FEED_PAGE_STATE.ERROR,
    );
  });

  it("only reports no-projects for an empty catalog with a successful empty feed", () => {
    expect(
      deriveFeedPageState({ catalog: ok([]), feed: ok(page([], 0)) }),
    ).toBe(FEED_PAGE_STATE.NO_PROJECTS);
  });

  it("lets rows or a positive total win over an empty picker", () => {
    expect(
      deriveFeedPageState({
        catalog: ok([]),
        feed: ok(page([{ cluster_id: "c" }])),
      }),
    ).toBe(FEED_PAGE_STATE.FEED);
    // out-of-range page: total says there is data even if this page is empty
    expect(
      deriveFeedPageState({ catalog: ok([]), feed: ok(page([], 3)) }),
    ).toBe(FEED_PAGE_STATE.FEED);
  });

  it("keeps the ordinary empty feed when projects exist", () => {
    expect(
      deriveFeedPageState({
        catalog: ok([{ value: "p", label: "P" }]),
        feed: ok(page([], 0)),
      }),
    ).toBe(FEED_PAGE_STATE.FEED);
  });

  it("treats a malformed feed envelope as a failed read, never as no-projects", () => {
    // a 200 whose `result` is missing selects to undefined
    expect(deriveFeedPageState({ catalog: ok([]), feed: ok(undefined) })).toBe(
      FEED_PAGE_STATE.ERROR,
    );
    expect(deriveFeedPageState({ catalog: ok([]), feed: ok({}) })).toBe(
      FEED_PAGE_STATE.ERROR,
    );
    expect(
      deriveFeedPageState({
        catalog: ok([]),
        feed: ok({ data: [], total: "0" }),
      }),
    ).toBe(FEED_PAGE_STATE.ERROR);
    expect(
      deriveFeedPageState({
        catalog: ok([]),
        feed: ok({ data: null, total: 0 }),
      }),
    ).toBe(FEED_PAGE_STATE.ERROR);
  });
});
