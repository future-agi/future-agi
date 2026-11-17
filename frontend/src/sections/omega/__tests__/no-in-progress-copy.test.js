import { describe, expect, it } from "vitest";

import { relative, sources, sweep } from "./copy-sweep";

// HIDE-UNFINISHED-FA (owner, 2026-10-19: "I don't want anything gimmicky / in
// progress / preview / coming soon"): no text a customer can read in the
// Omega tabs announces work in progress. A part that does not work is hidden
// (omega_rollout/readiness.py), never labelled. Empty states such as
// "No deployments yet" describe the customer's data, not our roadmap, and
// stay allowed.
//
// The sweep reads every non-test source file under sections/omega with its
// comments removed (copy-sweep.js), so a doc comment may still explain what
// is hidden and why.

// The COPY1 list of words a server message must never carry: it names them.
const SKIP_FILES = new Set(["shared/customerSafe.js"]);

const IN_PROGRESS = [
  /coming soon/i,
  /\bin preview\b/i,
  /\(preview\)/i,
  /\bpreview\s+(?:feature|release|build)\b/i,
  /\bbeta\b/i,
  /\bnot (?:available|reachable|set up|connected|supported) yet\b/i,
  /\b(?:isn't|aren't|doesn't|don't|can't|cannot|won't) [^"'`\n]{0,60}\byet\b/i,
  // Operator-setup hedges ("It may not be set up for this workspace yet").
  /\b(?:may|might) not be [^"'`\n]{0,60}\byet\b/i,
  /\bwhen it does\b/i,
  /\bstay tuned\b/i,
  /\bwork in progress\b/i,
];

describe("No in-progress copy in the Omega tabs", () => {
  const files = sources().filter((file) => !SKIP_FILES.has(relative(file)));

  it("reads the tab sources", () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it("finds no 'soon', 'preview', 'beta' or '... yet' announcement", () => {
    const hits = sweep(files, IN_PROGRESS).map(
      (hit) => `${hit.file}:${hit.line}: ${hit.text}`,
    );
    expect(hits).toEqual([]);
  });

  it("would catch the removed copy", () => {
    const removed = [
      "Deployments aren't reachable from Future AGI yet",
      "This deployment's sandbox API doesn't attach secrets yet, so none are sent.",
      "When it does, you'll pick them here by name",
      "Not available yet",
      "Future AGI couldn't start an Omega Console session. It may not be set up for this workspace yet. Try again in a moment.",
    ];
    for (const sample of removed) {
      expect(
        IN_PROGRESS.some((pattern) => pattern.test(sample)),
        sample,
      ).toBe(true);
    }
    for (const allowed of [
      "No deployments yet",
      "No files yet. Write one here.",
    ]) {
      expect(
        IN_PROGRESS.some((pattern) => pattern.test(allowed)),
        allowed,
      ).toBe(false);
    }
  });
});
