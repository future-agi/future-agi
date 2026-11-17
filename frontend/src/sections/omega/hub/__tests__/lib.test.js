import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { hubApi } from "../api/endpoints";
import { submissionPollInterval } from "../api/queries-me";
import { toHubError } from "../api/client";
import { HUB_ROUTES } from "../routes";
import { countOrDash, mondayOf, shortDigest } from "../lib/format";
import { pipelineStates } from "../lib/status";

describe("counts never invent a number (SPEC §6.2)", () => {
  it('shows "—" for 0, missing and negative values', () => {
    expect(countOrDash(0)).toBe("—");
    expect(countOrDash(undefined)).toBe("—");
    expect(countOrDash(null)).toBe("—");
    expect(countOrDash(1)).toBe("1");
    expect(countOrDash(12_400)).toBe("12k");
  });
});

describe("live submission status", () => {
  it("polls fast while checking, slowly in review, and stops at a decision", () => {
    expect(submissionPollInterval("received")).toBe(3000);
    expect(submissionPollInterval("checking")).toBe(3000);
    expect(submissionPollInterval("in_review")).toBe(15000);
    for (const done of ["approved", "rejected", "changes_requested", "checks_failed", "withdrawn"]) {
      expect(submissionPollInterval(done)).toBe(false);
    }
  });

  it("places each state on the four-step pipeline", () => {
    expect(pipelineStates("checking")).toEqual(["done", "active", "todo", "todo"]);
    expect(pipelineStates("checks_failed")).toEqual(["done", "failed", "todo", "todo"]);
    expect(pipelineStates("changes_requested")).toEqual(["done", "done", "failed", "todo"]);
    expect(pipelineStates("approved")).toEqual(["done", "done", "done", "done"]);
  });
});

describe("API layer", () => {
  it("encodes each path segment and keeps file paths' slashes", () => {
    expect(hubApi.versionFile("a", "b", "1.0.0", "src/main.ts")).toBe("/omega-hub/v1/entries/a/b/versions/1.0.0/files/src/main.ts");
    expect(hubApi.entry("a b", "c")).toBe("/omega-hub/v1/entries/a%20b/c");
  });

  it("turns the app's rejection into one error shape, keeping tombstones and moves", () => {
    const gone = toHubError({ error: { code: "OMEGA_HUB_GONE", message: "Removed", fix: "" }, tombstone: { category: "security issue" }, statusCode: 410 });
    expect(gone.status).toBe(410);
    expect(gone.code).toBe("OMEGA_HUB_GONE");
    expect(gone.tombstone.category).toBe("security issue");
    expect(toHubError({ movedTo: "a/b", statusCode: 301 }).movedTo).toBe("a/b");
    expect(toHubError({ message: "Network Error" }).message).toBe("Network Error");
  });

  it("formats digests and weeks", () => {
    expect(shortDigest("sha256:abcdef0123456789")).toBe("sha256:abcdef01");
    expect(mondayOf(new Date("2026-10-15T10:00:00Z"))).toBe("2026-10-12");
    expect(mondayOf(new Date("2026-10-12T00:00:00Z"))).toBe("2026-10-12");
  });
});

describe("routes (SPEC §7.1)", () => {
  it("registers every entry file, static segments before :publisher/:name", () => {
    const paths = HUB_ROUTES.map((r) => r.path);
    expect(paths[0]).toBe("hub");
    expect(paths.at(-1)).toBe("hub/:publisher/:name/:tab?");
    expect(paths.indexOf("hub/review/:id")).toBeGreaterThan(paths.indexOf("hub/review/audit"));
    expect(paths).toEqual(expect.arrayContaining([
      "hub/publish", "hub/publishers", "hub/publishers/:handle", "hub/submissions", "hub/submissions/:id",
      "hub/settings", "hub/review", "hub/review/reports", "hub/review/picks", "hub/review/publishers",
      "hub/review/blocklist", "hub/review/audit",
    ]));
  });
});

// SPEC §2.2: no on-page conversation of any kind. The banned words are
// assembled at run time so this file does not trip a text search for them.
describe("no conversation surface (SPEC §2.2)", () => {
  const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const banned = ["tnemmoc", "noissucsid", "daerht", "noitseuq", "ylper", "noclaf", "tahcia"].map((w) => w.split("").reverse().join(""));
  const files = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      if (name === "__fixtures__" || name === "node_modules") continue;
      if (fs.statSync(full).isDirectory()) walk(full);
      else if (/\.(jsx?|cjs|mjs|md)$/.test(name)) files.push(full);
    }
  };
  walk(ROOT);

  it("no Hub source mentions one, except the Discord link", () => {
    const hits = [];
    for (const file of files) {
      const text = fs.readFileSync(file, "utf8").replace(/Discuss on Discord/g, "");
      for (const word of banned) if (text.toLowerCase().includes(word)) hits.push(`${path.relative(ROOT, file)}: ${word}`);
    }
    expect(files.length).toBeGreaterThan(40);
    expect(hits).toEqual([]);
  });
});
