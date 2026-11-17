import React from "react";
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import official from "../__fixtures__/entry-official.json";
import KeysPanel from "../components/KeysPanel";
import { renderAt } from "./render";

// What each key is for is one or two short lines. Where the keys lie
// several to a row (a whole-width Keys card on a narrow page) each column
// is narrow, and a description used to end with one word alone on its last
// line ("on / Anthropic."). A browser that can now evens the lines out, so
// the last word keeps company; one that cannot wraps as before.

// A selector jsdom cannot match (a pseudo-class or element) never applies.
function matches(node, selector) {
  try {
    return node.matches(selector);
  } catch {
    return false;
  }
}

// What the page's style rules declare for `node` outside any media or
// container query: `property` as written, or "".
function declared(node, property) {
  let value = "";
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules || [])) {
      if (rule.selectorText && matches(node, rule.selectorText)) {
        value = rule.style.getPropertyValue(property) || value;
      }
    }
  }
  return value;
}

describe("Keys: each key's description", () => {
  it.each([
    { where: "spread across a whole-width card", spread: true },
    { where: "one to a row in the side column", spread: false },
  ])(
    "evens out its lines so its last word does not sit alone, $where",
    ({ spread }) => {
      const { keys, models } = official.latest.manifest;
      renderAt(<KeysPanel keys={keys} models={models} spread={spread} />);
      const card = screen.getByRole("region", { name: "Keys" });
      for (const key of keys) {
        const why = within(card).getByText(key.why);
        expect(declared(why, "text-wrap")).toBe("pretty");
      }
    },
  );
});
