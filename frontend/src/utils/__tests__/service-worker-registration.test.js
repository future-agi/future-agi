import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

const main = readFileSync("src/main.jsx", "utf8");
const start = main.indexOf("// Register service worker in production");
const end = main.indexOf("\n// ------", start);
const block = main.slice(start, end);

// Execute the shipped registration block with browser globals stubbed.
function runRegistration(readyState) {
  const registered = [];
  const listeners = {};
  runInNewContext(block, {
    CURRENT_ENVIRONMENT: "production",
    logger: { debug() {}, warn() {} },
    setInterval() {},
    document: { readyState },
    window: {
      addEventListener(type, listener) {
        listeners[type] = listener;
      },
    },
    navigator: {
      serviceWorker: {
        register(url) {
          registered.push(url);
          return Promise.resolve({ addEventListener() {}, update() {} });
        },
      },
    },
  });
  return { registered, listeners };
}

describe("service worker registration", () => {
  it("registers immediately when the page has already loaded", () => {
    const { registered, listeners } = runRegistration("complete");
    expect(registered).toEqual(["/service-worker.js"]);
    expect(listeners.load).toBeUndefined();
  });

  it.each(["loading", "interactive"])(
    "waits for the load event when the document is %s",
    (readyState) => {
      const { registered, listeners } = runRegistration(readyState);
      expect(registered).toEqual([]);
      listeners.load();
      expect(registered).toEqual(["/service-worker.js"]);
    },
  );
});
