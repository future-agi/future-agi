// W4 C3: the token page's pure helpers.
import { describe, expect, it } from "vitest";
import {
  answerProblem,
  asText,
  doneLine,
  errorCopy,
  inviteLine,
  isAnswered,
  keyAction,
  valueForKey,
} from "../reviewLinkModel";

const STAR = {
  id: "q",
  name: "Quality",
  type: "star",
  settings: { no_of_stars: 5 },
};
const THUMBS = {
  id: "c",
  name: "Correct?",
  type: "thumbs_up_down",
  settings: {},
};
const CAT = {
  id: "p",
  name: "Pick",
  type: "categorical",
  settings: { options: [{ label: "A" }, { label: "Tie" }, { label: "B" }] },
};

describe("answers", () => {
  it("knows a complete answer per label type", () => {
    expect(isAnswered(STAR, { rating: 4 })).toBe(true);
    expect(isAnswered(STAR, { rating: 0 })).toBe(false);
    expect(isAnswered(THUMBS, { value: "down" })).toBe(true);
    expect(isAnswered(CAT, { selected: [] })).toBe(false);
    expect(isAnswered({ type: "text" }, { text: " x " })).toBe(true);
    expect(isAnswered({ type: "numeric" }, { value: 0 })).toBe(true);
  });

  it("names the first unanswered label and refuses unknown ones", () => {
    expect(
      answerProblem([STAR, THUMBS], [{ label_id: "q", value: { rating: 3 } }]),
    ).toBe("Answer “Correct?” first.");
    expect(answerProblem([STAR], [{ label_id: "zz", value: {} }])).toMatch(
      /isn't part/,
    );
    expect(
      answerProblem([STAR], [{ label_id: "q", value: { rating: 3 } }]),
    ).toBeNull();
  });

  it("maps keys 1-9 to the first label's choices", () => {
    expect(valueForKey(CAT, 3)).toEqual({ selected: ["B"] });
    expect(valueForKey(CAT, 4)).toBeNull();
    expect(valueForKey(THUMBS, 2)).toEqual({ value: "down" });
    expect(valueForKey(STAR, 5)).toEqual({ rating: 5 });
    expect(valueForKey(STAR, 6)).toBeNull();
  });
});

describe("words", () => {
  it("header, done and errors", () => {
    expect(
      inviteLine({
        invitedBy: "Maya Chen",
        total: 6,
        expiresAt: "2026-10-29T12:00:00",
      }),
    ).toBe("Invited by Maya Chen · 6 items · expires Thu 29 Oct");
    expect(doneLine(6)).toBe(
      "Thanks, you reviewed 6 items. You can close this tab.",
    );
    expect(errorCopy(404, "This item isn't part of your review.")).toBe(
      "This item isn't part of your review.",
    );
    expect(errorCopy(404)).toBe("Link not found");
    expect(errorCopy(410)).toBe("Link expired");
    expect(errorCopy(403)).toBe(
      "This review link is waiting for an admin to approve it.",
    );
  });
});

describe("keyAction", () => {
  const PAIR = { id: "p", name: "Better", settings: { kind: "pairwise" } };
  const sides = [
    { slot: 1, side: "B" },
    { slot: 2, side: "A" },
  ];
  it("J and K move through the sample", () => {
    expect(keyAction("j", STAR)).toEqual({ move: 1 });
    expect(keyAction("k", STAR)).toEqual({ move: -1 });
  });
  it("1-9 answer the first label with its own choices", () => {
    expect(keyAction("1", THUMBS)).toEqual({ value: { value: "up" } });
    expect(keyAction("2", THUMBS)).toEqual({ value: { value: "down" } });
    expect(keyAction("3", THUMBS)).toBeNull();
    expect(keyAction("4", STAR)).toEqual({ value: { rating: 4 } });
    expect(keyAction("x", STAR)).toBeNull();
    expect(keyAction("1", null)).toBeNull();
  });
  it("pairwise keys store the blind response's side, 2 is a tie", () => {
    expect(keyAction("1", PAIR, sides)).toEqual({
      value: { selected: ["B"] },
    });
    expect(keyAction("2", PAIR, sides)).toEqual({
      value: { selected: ["Tie"] },
    });
    expect(keyAction("3", PAIR, sides)).toEqual({
      value: { selected: ["A"] },
    });
    expect(keyAction("4", PAIR, sides)).toBeNull();
  });
  it("asText keeps strings and pretty-prints objects", () => {
    expect(asText(null)).toBe("");
    expect(asText("a")).toBe("a");
    expect(asText({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
});
