import React from "react";
import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HubReviewPage from "../HubReviewPage";
import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import me from "../__fixtures__/me-maintainer.json";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// On a page too narrow for the Decision card to sit beside the review, a
// sticky bar at the bottom of the window carries the decision's button.
// While the Decision card itself is on screen, the bar steps out of view:
// the same button is in front of the reviewer, and the bar would only cover
// the card's lower edge. Once the card scrolls away, the bar comes back. The
// card is on screen once half of it shows or, for a card taller than the
// window, once it fills half the window; a sliver at the window's edge is
// not enough. The bar keeps its place in the page and the room kept free for
// it at the bottom of the scroller, so nothing moves as it goes and comes
// back, and it stays while focus is in it. Where the browser has no
// IntersectionObserver, the bar stays as it always was.

const BASE = `/omega-hub/v1/review/submissions/${review.id}`;
const current = {
  ...review,
  status: "in_review",
  previousVersion: null,
  draftDisclosure: review.confirmedDisclosure,
};
const routes = () => [
  ["get", "/omega-hub/v1/me", me],
  ["get", "/omega-hub/v1/rules", rules],
  ["get", BASE, current],
  ["get", `${BASE}/diff`, { diff: null }],
  ["get", new RegExp(`^${BASE}/files/`), "x = 1\n"],
];

// The review in a box that scrolls, as the app's main area does.
const openReview = () =>
  renderAt(
    <main data-testid="page-scroller" style={{ overflow: "auto" }}>
      <HubReviewPage />
    </main>,
    {
      path: "/dashboard/hub/review/:id",
      at: `/dashboard/hub/review/${review.id}`,
    },
  );

// Narrower than the lg breakpoint, where the review keeps a sticky decision
// bar. Returns the undo.
function narrowWindow() {
  const original = window.matchMedia.getMockImplementation();
  window.matchMedia.mockImplementation((query) => ({
    ...original(query),
    matches: /max-width/.test(query),
  }));
  return () => window.matchMedia.mockImplementation(original);
}

// A stand-in IntersectionObserver that reports only when a case says what
// shows (jsdom has no layout).
const observers = [];
class FakeIntersectionObserver {
  constructor(callback, options) {
    this.callback = callback;
    this.options = options;
    this.targets = new Set();
    this.disconnected = false;
    observers.push(this);
  }

  observe(node) {
    this.targets.add(node);
  }

  unobserve(node) {
    this.targets.delete(node);
  }

  disconnect() {
    this.disconnected = true;
    this.targets.clear();
  }

  takeRecords() {
    return [];
  }
}

const card = () => screen.getByTestId("hub-decision-form");
const bar = () => screen.getByTestId("hub-decision-bar");
const watching = (node) =>
  observers.filter((observer) => observer.targets.has(node));

// Tells whatever watches the Decision card that `seen` px of it show, of a
// card `height` px tall, in a window `room` px tall. Where nothing watches
// the card, nothing happens.
function cardShows(seen, { height = 400, room = 800 } = {}) {
  const target = card();
  const entry = {
    target,
    isIntersecting: seen > 0,
    intersectionRatio: Math.min(1, seen / height),
    boundingClientRect: { height },
    intersectionRect: { height: seen },
    rootBounds: { height: room },
    time: 0,
  };
  for (const observer of watching(target))
    act(() => observer.callback([entry], observer));
}

// The bar is out of view: still in the page, sticky in its place, but not
// drawn, and neither its region nor its button is there for a screen reader
// or for Tab.
function expectOutOfView() {
  const node = bar();
  expect(getComputedStyle(node).visibility).toBe("hidden");
  expect(getComputedStyle(node).position).toBe("sticky");
  expect(
    screen.queryByRole("region", { name: "Decision shortcut" }),
  ).toBeNull();
}

function expectInView() {
  const node = bar();
  expect(getComputedStyle(node).visibility).not.toBe("hidden");
  expect(getComputedStyle(node).position).toBe("sticky");
  expect(screen.getByRole("region", { name: "Decision shortcut" })).toBe(node);
}

let restore = null;
beforeEach(() => {
  observers.length = 0;
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  restore = narrowWindow();
});
afterEach(() => {
  restore?.();
  restore = null;
  vi.unstubAllGlobals();
});

describe("Review item on a narrow page: the decision bar beside the Decision card", () => {
  it("steps out of view while the Decision card is on screen, keeping its place, and comes back once the card scrolls away", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    expectInView();
    const shown = bar();
    const scroller = screen.getByTestId("page-scroller");
    const room = scroller.style.scrollPaddingBottom;
    expect(room).toMatch(/^[1-9]\d*px$/);
    cardShows(400);
    expectOutOfView();
    // The card's own button is the one in front of the reviewer.
    expect(
      within(card()).getByRole("button", { name: "Choose a decision" }),
    ).toBeInTheDocument();
    // The same bar, never taken off the page, and the room kept for it
    // stays, so nothing moves.
    expect(bar()).toBe(shown);
    expect(scroller.style.scrollPaddingBottom).toBe(room);
    cardShows(0);
    expectInView();
    expect(bar()).toBe(shown);
    const choose = within(bar()).getByRole("button", {
      name: "Choose a decision",
    });
    fireEvent.click(choose);
    expect(card()).toHaveFocus();
    expect(calls.filter((call) => call.method === "post")).toEqual([]);
  });

  it("steps out of view for a card taller than the window once it fills half the window", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    cardShows(450, { height: 1400, room: 800 });
    expectOutOfView();
  });

  it("stays while only a sliver of the card shows at the window's edge", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    cardShows(40);
    expectInView();
  });

  it("stays in view while focus is in it, and steps out once focus leaves it", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    const choose = within(bar()).getByRole("button", {
      name: "Choose a decision",
    });
    act(() => choose.focus());
    cardShows(400);
    expectInView();
    expect(choose).toHaveFocus();
    act(() => choose.blur());
    expectOutOfView();
  });

  it("watches the Decision card only while the bar is on the page, and stops when the page closes", async () => {
    respond(routes());
    const view = openReview();
    await screen.findAllByTestId("hub-checklist-item");
    const watchers = watching(card());
    expect(watchers).toHaveLength(1);
    view.unmount();
    expect(watchers[0].disconnected).toBe(true);
  });

  it("on a wide page, draws no bar and watches nothing", async () => {
    restore();
    restore = null;
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    expect(screen.queryByTestId("hub-decision-bar")).toBeNull();
    expect(watching(card())).toEqual([]);
  });

  it("without IntersectionObserver, keeps the bar as it always was, and its button still takes the reviewer to the card", async () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    expectInView();
    fireEvent.click(
      within(bar()).getByRole("button", { name: "Choose a decision" }),
    );
    expect(card()).toHaveFocus();
  });
});
