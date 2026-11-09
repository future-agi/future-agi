/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import {
  parseViewValue,
  readViewState,
  useViewUrlState,
  writeViewState,
} from "../viewState";

const DEFAULTS = {
  density: "compact",
  q: "",
  failed: false,
  cols: [],
  page: 0,
};

describe("viewState pure helpers", () => {
  it("parses by the default's type and falls back silently", () => {
    expect(parseViewValue("1", false)).toBe(true);
    expect(parseViewValue("x", false)).toBe(false);
    expect(parseViewValue("3", 0)).toBe(3);
    expect(parseViewValue("abc", 0)).toBe(0);
    expect(parseViewValue("a,b", [])).toEqual(["a", "b"]);
    expect(readViewState("?density=tall&failed=1", DEFAULTS)).toEqual({
      density: "tall",
      q: "",
      failed: true,
      cols: [],
      page: 0,
    });
  });
  it("omits defaults and keeps other params", () => {
    expect(
      writeViewState(
        "?row=7&density=tall",
        { ...DEFAULTS, failed: true },
        DEFAULTS,
      ),
    ).toBe("row=7&failed=1");
  });
});

let api;
function Probe() {
  api = useViewUrlState(DEFAULTS);
  const loc = useLocation();
  return <span data-testid="s">{loc.search}</span>;
}

describe("useViewUrlState", () => {
  it("writes with replace, reports modified, resets", () => {
    render(
      <MemoryRouter initialEntries={["/x?tab=data&row=4"]}>
        <Probe />
      </MemoryRouter>,
    );
    expect(api[3]).toBe(false);
    act(() => api[1]({ density: "tall", cols: ["a", "b"] }));
    expect(screen.getByTestId("s").textContent).toBe(
      "?tab=data&row=4&density=tall&cols=a%2Cb",
    );
    expect(api[0].density).toBe("tall");
    expect(api[0].cols).toEqual(["a", "b"]);
    expect(api[3]).toBe(true);
    act(() => api[2]());
    expect(screen.getByTestId("s").textContent).toBe("?tab=data&row=4");
    expect(api[3]).toBe(false);
  });
});
