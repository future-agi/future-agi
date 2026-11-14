import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(path, "utf8");

describe("Appcues cleanup", () => {
  it("leaves no Appcues calls in the app or analytics helpers", () => {
    for (const path of [
      "index.html",
      "src/app.jsx",
      "src/utils/Mixpanel/mixpanel.js",
    ]) {
      expect(read(path)).not.toMatch(/Appcues/);
    }
  });
  it("retains startup URL ordering", () => {
    const html = read("index.html");
    expect(html.indexOf('id="saml-initial-marker-guard"')).toBeLessThan(
      html.indexOf('src="/src/entry.js"'),
    );
    expect(html).toContain('name="referrer" content="no-referrer"');
  });
});
