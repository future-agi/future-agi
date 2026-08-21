import "@testing-library/jest-dom";
import { vi } from "vitest";

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
 * Mock Web Storage. jsdom exposes a `localStorage` object here that has no
 * methods on it, so any module reading storage at import time dies during
 * collection rather than in a test — `states.jsx` builds a zustand store from
 * `JSON.parse(localStorage.getItem(...))` at module scope, which took five
 * evals test files down with `localStorage.getItem is not a function` before
 * a single assertion ran.
 */
const createStorageMock = () => {
  let store = {};
  return {
    getItem: (key) => (key in store ? store[key] : null),
    setItem: (key, value) => {
      store[key] = String(value);
    },
    removeItem: (key) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
    key: (i) => Object.keys(store)[i] ?? null,
    get length() {
      return Object.keys(store).length;
    },
  };
};

Object.defineProperty(window, "localStorage", {
  writable: true,
  configurable: true,
  value: createStorageMock(),
});
Object.defineProperty(window, "sessionStorage", {
  writable: true,
  configurable: true,
  value: createStorageMock(),
});

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
