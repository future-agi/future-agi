import { describe, expect, it } from "vitest";

import { fromControl } from "../grid-events";

function eventOn(html, selector) {
  document.body.innerHTML = html;
  return { event: { target: document.querySelector(selector) } };
}

describe("fromControl", () => {
  it("tells a click on a row's own button from a click on the row", () => {
    expect(
      fromControl(
        eventOn(
          '<div class="row"><button><span id="i"></span></button></div>',
          "#i",
        ),
      ),
    ).toBe(true);
    expect(
      fromControl(
        eventOn('<div class="row"><span id="c">name</span></div>', "#c"),
      ),
    ).toBe(false);
    expect(fromControl({ data: {} })).toBe(false);
  });
});
