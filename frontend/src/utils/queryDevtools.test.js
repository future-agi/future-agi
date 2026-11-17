import { describe, expect, it } from "vitest";

import { queryDevtoolsVisible } from "./queryDevtools";

describe("queryDevtoolsVisible", () => {
  it("shows the devtools in a plain development build", () => {
    expect(queryDevtoolsVisible({ DEV: true })).toBe(true);
  });

  it("never shows them in a production build", () => {
    expect(queryDevtoolsVisible({ DEV: false, PROD: true })).toBe(false);
    expect(
      queryDevtoolsVisible({
        DEV: false,
        VITE_REACT_QUERY_DEVTOOLS_ENABLED: "true",
      }),
    ).toBe(false);
  });

  it("never shows them on a stack that serves the embedded Omega console", () => {
    expect(queryDevtoolsVisible({ DEV: true, VITE_OMEGA_CONSOLE: "1" })).toBe(
      false,
    );
  });

  it("honours the explicit off switch", () => {
    expect(
      queryDevtoolsVisible({
        DEV: true,
        VITE_REACT_QUERY_DEVTOOLS_ENABLED: "false",
      }),
    ).toBe(false);
  });

  it("treats a missing env as off", () => {
    expect(queryDevtoolsVisible(undefined)).toBe(false);
  });
});
