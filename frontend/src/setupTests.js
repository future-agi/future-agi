import "@testing-library/jest-dom";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Register for every spec, including the lane runner's single-fork batches.
// RTL's import-time auto-cleanup can be cached across specs in one worker.
afterEach(cleanup);

// ApexCharts measures real DOM layout jsdom does not provide, so it throws on
// render. Stub react-apexcharts globally — chart tests assert the data/props
// fed to it, never its pixels.
vi.mock("react-apexcharts", () => ({
  default: () => null,
}));

/**
 * Web Storage shim (R3 G0, R2D-24). Node 26 ships an experimental
 * `localStorage` global without a backing file: `localStorage.clear` and
 * friends are undefined, so tests that reset storage threw. Every Node
 * version gets the same in-memory Storage: on Node 22 jsdom's own Storage
 * was kept, where `vi.spyOn(localStorage, "setItem")` stores an item named
 * "setItem" instead of replacing the method, so a test that blocks storage
 * passed on one Node and failed on the other.
 */
class MemoryStorage {
  constructor() {
    this.map = new Map();
  }

  get length() {
    return this.map.size;
  }

  key(i) {
    return [...this.map.keys()][i] ?? null;
  }

  getItem(k) {
    return this.map.has(String(k)) ? this.map.get(String(k)) : null;
  }

  setItem(k, v) {
    this.map.set(String(k), String(v));
  }

  removeItem(k) {
    this.map.delete(String(k));
  }

  clear() {
    this.map.clear();
  }
}
["localStorage", "sessionStorage"].forEach((name) => {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value: storage,
  });
  if (typeof window !== "undefined" && window !== globalThis)
    Object.defineProperty(window, name, {
      configurable: true,
      writable: true,
      value: storage,
    });
});

// Add any additional global test setup here
// For example, you might want to mock certain modules globally:

/**
 * Mock IntersectionObserver for components that use it
 * (like lazy loading components, infinite scroll, etc.)
 */
globalThis.IntersectionObserver = class IntersectionObserver {
  constructor() {}

  disconnect() {}

  observe() {}

  unobserve() {}
};

/**
 * Mock ResizeObserver for components that use it
 */
globalThis.ResizeObserver = class ResizeObserver {
  constructor() {}

  disconnect() {}

  observe() {}

  unobserve() {}
};

/**
 * Mock matchMedia for responsive components
 */
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // deprecated
    removeListener: vi.fn(), // deprecated
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});
