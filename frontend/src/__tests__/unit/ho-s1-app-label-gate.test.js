// HO S1 (the capability contract): app.jsx mounts the prototype label exactly once, and
// only through the static env ternary, so a build without prototype mode
// emits no label chunk and no label text.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";

const app = readFileSync(resolve(process.cwd(), "src/app.jsx"), "utf8");

describe("app.jsx prototype label gate", () => {
  it("holds exactly one <PrototypeLabel mount", () => {
    expect(app.match(/<PrototypeLabel\b/g)).toHaveLength(1);
  });

  it("imports the label lazily inside the static prototype env ternary", () => {
    const gate =
      /const PrototypeLabel =\s*import\.meta\.env\.VITE_ENVIRONMENT === "local" &&\s*import\.meta\.env\.VITE_PROTOTYPE_MODE === "true"\s*\?\s*React\.lazy\(\(\) => import\("src\/components\/prototype-label\/PrototypeLabel"\)\)\s*:\s*null;/;
    expect(app).toMatch(gate);
    // No other (static) import of the label or its model.
    expect(app.match(/prototype-label\//g)).toHaveLength(1);
  });

  it("mounts it inside ThemeProvider and outside the ErrorBoundary", () => {
    const theme = app.indexOf("<ThemeProvider>");
    const mount = app.indexOf("<PrototypeLabel />");
    const boundary = app.indexOf("<ErrorBoundary");
    expect(theme).toBeGreaterThan(-1);
    expect(mount).toBeGreaterThan(theme);
    expect(mount).toBeLessThan(boundary);
    expect(app.slice(mount - 200, mount)).toContain(
      "<Suspense fallback={null}>",
    );
  });
});
