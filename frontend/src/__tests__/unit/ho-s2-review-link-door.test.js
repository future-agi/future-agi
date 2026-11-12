// HO S2: the outside reviewer's page (/review/:token, W4-4) reads only
// prototype paths, so it loads and routes only in a prototype build, and the
// leak scan names its chunk. The checks read the sources: the gate is a static
// env expression the build folds (the R4Lab and Leaderboards precedent).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import {
  PROTOTYPE_ONLY_CHUNKS,
  prototypeChunksOf,
} from "../../../scripts/prototype-leak-scan.mjs";

const routes = readFileSync(
  resolve(process.cwd(), "src", "routes/sections/index.jsx"),
  "utf8",
);
const STATIC_GATE =
  /const PROTOTYPE_BUILD =\s*import\.meta\.env\.VITE_ENVIRONMENT === "local" &&\s*import\.meta\.env\.VITE_PROTOTYPE_MODE === "true";/;

describe("the review link door (prototype builds only)", () => {
  it("the page loads only inside the static gate", () => {
    expect(routes).toMatch(STATIC_GATE);
    expect(routes).toMatch(
      /const ReviewLinkPage = PROTOTYPE_BUILD\s*\?\s*lazyWithRetry\(\(\) => import\("src\/pages\/review-link\/ReviewLinkPage"\)\)\s*:\s*null;/,
    );
    expect(
      routes.match(/import\("src\/pages\/review-link\/ReviewLinkPage"\)/g),
    ).toHaveLength(1);
  });

  it("its route exists only when the page is loaded", () => {
    expect(routes).toMatch(
      /\.\.\.\(ReviewLinkPage\s*\?\s*\[\s*\{\s*path: "\/review\/:token",/,
    );
    expect(routes.match(/path: "\/review\/:token"/g)).toHaveLength(1);
  });

  it("the leak scan names the page's chunk", () => {
    expect(PROTOTYPE_ONLY_CHUNKS).toContain("ReviewLinkPage-");
    expect(
      prototypeChunksOf([
        "assets/ReviewLinkPage-a1b2c3.js",
        "assets/ReviewLinkView-a1b2c3.js",
        "assets/index-a1b2c3.js",
      ]),
    ).toEqual(["assets/ReviewLinkPage-a1b2c3.js"]);
  });
});
