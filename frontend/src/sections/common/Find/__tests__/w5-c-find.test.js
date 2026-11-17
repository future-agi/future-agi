import { describe, expect, it } from "vitest";
import {
  countLabel,
  countOccurrences,
  fieldRef,
  filterExpressionFor,
  isFindShortcut,
  ownsFindShortcut,
  quoteLiteral,
} from "../findModel";

describe("w5-c find model", () => {
  it("Cmd/Ctrl+F only, without Shift or Alt", () => {
    expect(isFindShortcut({ key: "f", metaKey: true })).toBe(true);
    expect(isFindShortcut({ key: "F", ctrlKey: true })).toBe(true);
    expect(isFindShortcut({ key: "f" })).toBe(false);
    expect(isFindShortcut({ key: "f", metaKey: true, shiftKey: true })).toBe(
      false,
    );
    expect(isFindShortcut({ key: "g", metaKey: true })).toBe(false);
  });

  it("the panel owns the shortcut only when focus or the target is inside it", () => {
    const panel = document.createElement("div");
    const inside = document.createElement("button");
    const outside = document.createElement("button");
    panel.appendChild(inside);
    document.body.append(panel, outside);
    const e = (target) => ({ key: "f", metaKey: true, target });
    expect(ownsFindShortcut(e(inside), panel, document.body)).toBe(true);
    expect(ownsFindShortcut(e(outside), panel, inside)).toBe(true);
    expect(ownsFindShortcut(e(outside), panel, outside)).toBe(false);
    expect(ownsFindShortcut(e(inside), null, inside)).toBe(false);
    // A click on text inside the panel focuses nothing (focus = body).
    expect(ownsFindShortcut(e(document.body), panel, document.body, true)).toBe(
      true,
    );
    expect(
      ownsFindShortcut(e(document.body), panel, document.body, false),
    ).toBe(false);
    // A button outside keeps focus after a click on text inside (the
    // button that opened the panel): the last click decides.
    expect(ownsFindShortcut(e(outside), panel, outside, true)).toBe(true);
    // A field outside that takes typing wins over the last click.
    const field = document.createElement("input");
    document.body.append(field);
    expect(ownsFindShortcut(e(field), panel, field, true)).toBe(false);
    const area = document.createElement("textarea");
    document.body.append(area);
    expect(ownsFindShortcut(e(area), panel, area, true)).toBe(false);
    const box = document.createElement("input");
    box.type = "checkbox";
    document.body.append(box);
    expect(ownsFindShortcut(e(box), panel, box, true)).toBe(true);
  });

  it("count label", () => {
    expect(countLabel(2, 7, "ord")).toBe("3 of 7");
    expect(countLabel(9, 7, "ord")).toBe("7 of 7");
    expect(countLabel(0, 0, "ord")).toBe("No matches");
    expect(countLabel(0, 0, "  ")).toBe("");
    expect(countLabel(0, 1200, "a")).toBe("1 of 1,200");
  });

  it("counts case-insensitive, non-overlapping occurrences", () => {
    expect(countOccurrences("Order ORD-1 and ord-1", "ord-1")).toBe(2);
    expect(countOccurrences("aaaa", "aa")).toBe(2);
    expect(countOccurrences("abc", "")).toBe(0);
    expect(countOccurrences(null, "x")).toBe(0);
  });

  it("Filter rows with this writes the expression text with quotes escaped", () => {
    expect(quoteLiteral('say "hi" \\ bye')).toBe('"say \\"hi\\" \\\\ bye"');
    expect(fieldRef("customer_message")).toBe("customer_message");
    expect(fieldRef("customer message")).toBe("`customer message`");
    expect(filterExpressionFor("ORD-1042", "customer_message")).toBe(
      'output contains "ORD-1042" or customer_message contains "ORD-1042"',
    );
    expect(filterExpressionFor("x")).toBe('output contains "x"');
  });
});
