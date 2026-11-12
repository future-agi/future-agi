// W11-QS-RELATIVE-API-BASE: a single-origin self-host (deploy/README.md,
// "Single-origin via reverse proxy route-split") sets VITE_HOST_API=/api. The
// app calls /api on its own origin, but a printed command or SDK base runs
// outside the browser, where "/api" names nothing. Every printed base is this
// page's origin plus the configured path.
import { describe, expect, it, vi } from "vitest";

import { QUICKSTART_PROMPT } from "src/components/empty-state/quickstarts";
import { FI_BASE_URL, apiBaseOf } from "../apiBase";
import { fiRequest, requestSnippets } from "../requestSnippets";

// vi.mock is hoisted above the imports, so the literal is repeated in the factory.
vi.mock("src/config-global", async (importOriginal) => ({
  ...(await importOriginal()),
  HOST_API: "/api/",
}));

const ORIGIN = window.location.origin;

describe("a path-only HOST_API (single-origin self-host)", () => {
  it("prints this page's origin plus the path as the API base", () => {
    expect(ORIGIN).toMatch(/^https?:\/\/[^/]+$/);
    expect(FI_BASE_URL).toBe(`${ORIGIN}/api`);
  });

  it("reaches every printed command", () => {
    const request = fiRequest("/model-hub/datasets/imports/", {});
    expect(request.url).toBe(`${ORIGIN}/api/model-hub/datasets/imports/`);
    expect(requestSnippets(request).curl).toContain(
      `"${ORIGIN}/api/model-hub/datasets/imports/"`,
    );
  });

  it("reaches the prompt quickstart's FI_BASE_URL", () => {
    const code = QUICKSTART_PROMPT.snippets[0].code;
    expect(code).toContain(`os.environ["FI_BASE_URL"] = "${ORIGIN}/api"`);
    expect(code).not.toContain('"/api"');
  });
});

describe("apiBaseOf", () => {
  const PAGE = "https://app.example.com";

  it.each([
    ["/api", "https://app.example.com/api"],
    ["/api/", "https://app.example.com/api"],
    ["  /api  ", "https://app.example.com/api"],
    ["/", "https://app.example.com"],
    ["//api.example.com/", "https://api.example.com"],
  ])("resolves %j against the page origin", (host, expected) => {
    expect(apiBaseOf(host, PAGE)).toBe(expected);
  });

  it.each([
    ["https://api.example.com/", "https://api.example.com"],
    [" http://127.0.0.1:8000 ", "http://127.0.0.1:8000"],
    ["", ""],
    [undefined, ""],
    [null, ""],
  ])("keeps %j as configured, trimmed", (host, expected) => {
    expect(apiBaseOf(host, PAGE)).toBe(expected);
  });

  it("keeps a path-only host when the page has no origin to resolve it against", () => {
    expect(apiBaseOf("/api/", "null")).toBe("/api");
  });
});
