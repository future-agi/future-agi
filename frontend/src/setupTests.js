import "@testing-library/jest-dom";
import { vi } from "vitest";

// ApexCharts measures real DOM layout jsdom does not provide, so it throws on
// render. Stub react-apexcharts globally — chart tests assert the data/props
// fed to it, never its pixels.
vi.mock("react-apexcharts", () => ({
  default: () => null,
}));

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
