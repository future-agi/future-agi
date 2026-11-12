import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { returnToClient } from "./consentApi";
vi.mock("src/utils/axios", () => ({ default: {} }));
const assign = vi.fn();
beforeEach(() => {
  assign.mockClear();
  vi.stubGlobal("window", { location: { assign } });
});
afterEach(() => vi.unstubAllGlobals());
it.each([
  [
    "http://127.0.0.1:4321/callback?code=opaque&state=s",
    "http://127.0.0.1:4321/callback",
  ],
  [
    "synthetic-desktop://callback?error=access_denied&state=s",
    "synthetic-desktop://callback",
  ],
  [
    "https://client.example.test/callback?existing=keep&code=opaque",
    "https://client.example.test/callback?existing=keep",
  ],
])(
  "returns to the registered callback preserving the API's response (%s)",
  (target, registered) => {
    returnToClient(target, registered);
    expect(assign).toHaveBeenCalledWith(target);
  },
);
it.each([
  // eslint-disable-next-line no-script-url -- refusal fixture, never navigated
  ["javascript:alert(1)", "javascript:alert(1)"],
  ["data:text/html,x", "data:text/html,x"],
  [
    "https://different.invalid/callback?code=opaque",
    "https://client.example.test/callback",
  ],
  [
    "http://127.0.0.1:4322/callback?code=opaque",
    "http://127.0.0.1:4321/callback",
  ],
  ["https://client.example.test/other", "https://client.example.test/callback"],
  [
    "https://client.example.test/callback?code=opaque",
    "https://client.example.test/callback?existing=keep",
  ],
])("refuses a nonmatching or unsafe callback (%s)", (target, registered) => {
  expect(() => returnToClient(target, registered)).toThrow();
  expect(assign).not.toHaveBeenCalled();
});
